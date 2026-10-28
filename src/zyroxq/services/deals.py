"""Deal lifecycle after negotiation: Cart Mandate → policy gate → Payment Mandate → HSP settlement →
escrow funding → proof verification → payouts. Every transition is written to the audit log."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..chain.escrow import EscrowError
from ..hsp.cart import build_cart_contents
from ..hsp.webhook import verify_webhook_signature
from ..models import (
    AuditEvent,
    Campaign,
    CampaignSpec,
    Deal,
    DealState,
    DealTerms,
    InfluencerProfile,
    Offer,
    Payout,
    Proof,
)
from ..policy import PolicyResult, check_cart, check_terms, needs_human_approval, performance_payout
from ..verifier.verify import all_passed, check_video
from ..verifier.youtube import parse_video_id
from ..wallet.gateway import GatewayRejected
from ..wallet.signer import sign_payment_mandate, to_token_units
from .context import AppContext

log = logging.getLogger(__name__)

# Deals whose cap counts against the campaign budget
COMMITTED = {
    DealState.CART_CREATED, DealState.AWAITING_APPROVAL, DealState.AWAITING_CHECKOUT, DealState.SIGNED,
    DealState.FUNDING, DealState.FUNDED, DealState.PROOF_SUBMITTED, DealState.PROOF_REJECTED,
    DealState.VERIFIED, DealState.FIXED_RELEASED, DealState.DISPUTED, DealState.COMPLETED,
}  # fmt: skip


class DealError(Exception):
    pass


class DealService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = asyncio.Lock()  # serializes escrow/ledger mutations

    # ---- helpers -----------------------------------------------------------

    async def audit(self, campaign_id: str, deal_id: str | None, actor: str, kind: str, /, **detail: Any) -> None:
        await self.ctx.store.add_event(
            AuditEvent(at=self.ctx.clock.now(), campaign_id=campaign_id, deal_id=deal_id, actor=actor, kind=kind,
                       detail=json.loads(json.dumps(detail, default=str)))
        )  # fmt: skip

    async def save(self, deal: Deal, state: DealState | None = None) -> Deal:
        if state is not None:
            deal.state = state
        deal.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_deal(deal)
        return deal

    async def get(self, deal_id: str) -> Deal:
        deal = await self.ctx.store.get_deal(deal_id)
        if deal is None:
            raise DealError(f"unknown deal {deal_id}")
        return deal

    async def campaign(self, campaign_id: str) -> Campaign:
        c = await self.ctx.store.get_campaign(campaign_id)
        if c is None:
            raise DealError(f"unknown campaign {campaign_id}")
        return c

    async def committed_spend(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        deals = await self.ctx.store.list_deals(campaign_id)
        return sum((d.terms.cap for d in deals if d.terms and d.id != exclude and d.state in COMMITTED), Decimal("0"))

    def profile(self, influencer_id: str) -> InfluencerProfile:
        return self.ctx.registry[influencer_id].profile

    async def notify_influencer(self, deal: Deal, message_type: str, /, **data: Any) -> None:
        """Tell the influencer's agent over A2A. Best effort: a down agent never blocks settlement."""
        reg = self.ctx.registry.get(deal.influencer_id)
        if reg is None:
            return
        try:
            await self.ctx.a2a.send(reg.agent_url, {"type": message_type, "deal_id": deal.id, **json.loads(json.dumps(data, default=str))})
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", f"a2a_{message_type}_sent", **data)
        except Exception as e:  # noqa: BLE001
            log.warning("A2A notify %s failed: %s", message_type, e)

    async def refresh_campaign_status(self, campaign_id: str) -> None:
        c = await self.campaign(campaign_id)
        deals = await self.ctx.store.list_deals(campaign_id)
        states = {d.state for d in deals}
        if c.status in ("planning", "negotiating", "failed") and not (states - {DealState.NEGOTIATING}):
            return
        terminal = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_FAILED, DealState.COMPLETED, DealState.EXPIRED_REFUNDED}
        if DealState.AWAITING_APPROVAL in states:
            status = "awaiting_approval"
        elif states & {DealState.AWAITING_CHECKOUT, DealState.SIGNED, DealState.FUNDING, DealState.CART_CREATED}:
            status = "funding"
        elif states and states <= terminal:
            status = "done"
        else:
            status = "active"
        if status != c.status:
            c.status = status  # type: ignore[assignment]
            await self.ctx.store.save_campaign(c)

    # ---- negotiation bookkeeping ------------------------------------------

    async def record_round(self, deal: Deal, offer: Offer, reply: dict[str, Any], actor: str) -> None:
        deal.rounds.append({"offer": offer.model_dump(mode="json"), "reply": reply})
        await self.save(deal)
        await self.audit(deal.campaign_id, deal.id, actor, "negotiation_round", round=offer.round,
                         fixed=offer.fixed_fee, cpm=offer.cpm, reply=reply.get("kind"), reason=reply.get("reason", ""))  # fmt: skip

    # ---- cart mandate + policy gate + payment mandate ------------------------

    async def create_cart(self, deal: Deal) -> Deal:
        terms = self._terms(deal)
        contents = build_cart_contents(
            cart_mandate_id=deal.id,
            payment_request_id=f"PAY-{deal.id}",
            merchant_name=self.ctx.settings.hsp_merchant_name,
            token=self.ctx.token,
            pay_to=self.ctx.escrow.address,
            items=[("Fixed fee", terms.fixed_fee), ("Performance fee (max)", terms.cap - terms.fixed_fee)],
            expires_at=self.ctx.clock.now() + timedelta(hours=2),
        )
        order = await self.ctx.gateway.create_order(contents)
        deal.cart_contents = contents
        deal.cart_mandate_id = contents["id"]
        deal.payment_request_id = order.payment_request_id
        deal.payment_url = order.payment_url
        await self.save(deal, DealState.CART_CREATED)
        await self.audit(deal.campaign_id, deal.id, "zyroxq-merchant", "cart_mandate_created",
                         total=terms.cap, pay_to=self.ctx.escrow.address, gateway=self.ctx.gateway.name,
                         payment_request_id=order.payment_request_id)  # fmt: skip
        return deal

    async def gate(self, deal: Deal, spec: CampaignSpec, agreed: Offer) -> PolicyResult:
        """Everything that must hold before the wallet may sign. Failure → BLOCKED, nothing signed."""
        terms = self._terms(deal)
        assert deal.cart_contents is not None
        result = check_terms(spec, terms, self.profile(deal.influencer_id), agreed)
        cart = check_cart(spec, terms, deal.cart_contents, self.ctx.token, self.ctx.escrow.address,
                          await self.committed_spend(deal.campaign_id, exclude=deal.id), self.ctx.clock.now())  # fmt: skip
        result.violations += cart.violations
        await self.audit(deal.campaign_id, deal.id, "policy", "policy_gate", ok=result.ok, violations=result.violations)
        if not result.ok:
            deal.policy_violations = result.violations
            await self.save(deal, DealState.BLOCKED)
        return result

    def approval_needed(self, deals: list[Deal]) -> bool:
        total = sum((self._terms(d).cap for d in deals), Decimal("0"))
        return needs_human_approval(total, self.ctx.settings.approval_threshold_usd)

    async def await_approval(self, deal: Deal) -> None:
        await self.save(deal, DealState.AWAITING_APPROVAL)

    async def sign_and_submit(self, deal: Deal) -> Deal:
        """Fixed code path after the policy gate (and human approval when required)."""
        assert deal.cart_contents is not None and deal.payment_request_id is not None
        if not self.ctx.gateway.agent_signing:
            await self.save(deal, DealState.AWAITING_CHECKOUT)
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", "awaiting_hosted_checkout", payment_url=deal.payment_url)
            return deal
        now = int(self.ctx.clock.now().timestamp())
        mandate = sign_payment_mandate(self.ctx.advertiser_key, deal.cart_contents, deal.payment_request_id, self.ctx.token, now - 60, now + 3600)
        await self.save(deal, DealState.SIGNED)
        await self.audit(deal.campaign_id, deal.id, "wallet", "payment_mandate_signed", payer=mandate.payer,
                         value=mandate.authorization["value"], to=mandate.authorization["to"], scheme="x402 exact / EIP-3009")  # fmt: skip
        try:
            await self.ctx.gateway.submit_payment_mandate(mandate)
        except GatewayRejected as e:
            deal = await self.get(deal.id)
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_rejected", reason=str(e))
        return await self.get(deal.id)

    # ---- HSP webhook ---------------------------------------------------------

    async def handle_webhook(self, raw: bytes, signature_header: str | None) -> bool:
        now = int(self.ctx.clock.now().timestamp())
        if not verify_webhook_signature(signature_header, raw, self.ctx.gateway.webhook_secret, now=now):
            return False
        payload = json.loads(raw)
        if not await self.ctx.store.claim_webhook(payload["request_id"], payload["status"]):
            return True  # duplicate delivery
        await self.on_payment(payload)
        return True

    async def on_payment(self, p: dict[str, Any]) -> None:
        deal = await self.ctx.store.get_deal(p["cart_mandate_id"])
        if deal is None or deal.terms is None:
            log.warning("webhook for unknown deal %s", p.get("cart_mandate_id"))
            return
        status = p["status"]
        deal.payment_status = status
        await self.audit(deal.campaign_id, deal.id, "hsp", "payment_status", status=status, tx=p.get("tx_signature"), amount=p.get("pay_amount"))
        expected = (Decimal(p["order_amount"]), p["to_pay_address"].lower(), p["token"])
        if expected != (deal.terms.cap, self.ctx.escrow.address.lower(), deal.terms.asset):
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "settlement_mismatch", got=p)
            return
        if status == "payment-failed":
            await self.save(deal, DealState.PAYMENT_FAILED)
        elif status in ("payment-included", "payment-safe"):
            if deal.state in (DealState.SIGNED, DealState.AWAITING_CHECKOUT):
                await self.save(deal, DealState.FUNDING)
        elif status == "payment-finalized" and deal.state in (DealState.SIGNED, DealState.AWAITING_CHECKOUT, DealState.FUNDING):
            await self._fund_escrow(deal, p)
        else:
            await self.save(deal)
        await self.refresh_campaign_status(deal.campaign_id)

    async def _fund_escrow(self, deal: Deal, p: dict[str, Any]) -> None:
        terms = self._terms(deal)
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        async with self._lock:
            try:
                await asyncio.to_thread(self.ctx.escrow.register_deal, deal.id, p["payer_address"], terms.payee, cap_units,
                                        int(terms.publish_by.timestamp()))  # fmt: skip
                tx = await asyncio.to_thread(self.ctx.escrow.mark_funded, deal.id)
            except EscrowError as e:
                await self.save(deal, DealState.PAYMENT_FAILED)
                await self.audit(deal.campaign_id, deal.id, "escrow", "funding_failed", reason=str(e))
                return
        deal.funding_tx = p.get("tx_signature")
        await self.save(deal, DealState.FUNDED)
        await self.audit(deal.campaign_id, deal.id, "escrow", "escrow_funded", cap=terms.cap, escrow=self.ctx.escrow.address, tx=tx)
        await self.notify_influencer(deal, "funded", amount_locked=terms.cap, escrow=self.ctx.escrow.address, tx=deal.funding_tx)

    # ---- proof + payouts -----------------------------------------------------

    async def submit_proof(self, deal_id: str, video_ref: str) -> Deal:
        deal = await self.get(deal_id)
        if deal.state not in (DealState.FUNDED, DealState.PROOF_REJECTED):
            raise DealError(f"deal is {deal.state}, not awaiting proof")
        video_id = parse_video_id(video_ref)
        if video_id is None:
            raise DealError("not a YouTube URL or video id")
        spec = (await self.campaign(deal.campaign_id)).spec
        video = await self.ctx.videos.get_video(video_id)
        assert deal.agreed_at is not None
        checks = check_video(self._terms(deal), video, deal.agreed_at, spec.brand_handle)
        now = self.ctx.clock.now()
        deal.proof = Proof(video_id=video_id, submitted_at=now, checks=checks,
                           published_at=video.published_at if video else None, views=video.views if video else None)  # fmt: skip
        passed = all_passed(checks)
        await self.audit(deal.campaign_id, deal.id, "verifier", "proof_checked", video_id=video_id, passed=passed,
                         failed=[f"{c.name}: {c.detail}" for c in checks if not c.passed])  # fmt: skip
        if not passed:
            return await self.save(deal, DealState.PROOF_REJECTED)
        terms = self._terms(deal)
        deal.fixed_release_due = now + timedelta(hours=max(terms.min_live_hours, terms.review_window_hours))
        assert video is not None
        deal.performance_due = max(video.published_at + timedelta(days=terms.views_measured_after_days), deal.fixed_release_due)
        await self.save(deal, DealState.VERIFIED)
        await self.refresh_campaign_status(deal.campaign_id)
        return deal

    async def dispute(self, deal_id: str, reason: str) -> Deal:
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError("disputes are only possible during the review window")
        await self.audit(deal.campaign_id, deal.id, "human", "disputed", reason=reason)
        return await self.save(deal, DealState.DISPUTED)

    async def tick(self) -> int:
        """Scheduler: refunds, fixed-fee release, performance payout. Returns the number of actions taken."""
        actions = 0
        now = self.ctx.clock.now()
        for deal in await self.ctx.store.list_deals():
            try:
                if deal.state in (DealState.FUNDED, DealState.PROOF_REJECTED) and now > self._terms(deal).publish_by:
                    await self._refund_expired(deal)
                elif deal.state == DealState.VERIFIED and deal.fixed_release_due and now >= deal.fixed_release_due:
                    await self._release_fixed(deal)
                elif deal.state == DealState.FIXED_RELEASED and deal.performance_due and now >= deal.performance_due:
                    await self._release_performance(deal)
                else:
                    continue
                actions += 1
            except (EscrowError, DealError) as e:
                await self.audit(deal.campaign_id, deal.id, "escrow", "payout_error", reason=str(e))
            await self.refresh_campaign_status(deal.campaign_id)
        return actions

    async def _recheck(self, deal: Deal) -> tuple[bool, int]:
        assert deal.proof is not None and deal.agreed_at is not None
        spec = (await self.campaign(deal.campaign_id)).spec
        video = await self.ctx.videos.get_video(deal.proof.video_id)
        checks = check_video(self._terms(deal), video, deal.agreed_at, spec.brand_handle)
        return all_passed(checks), video.views if video else 0

    async def _release(self, deal: Deal, amount: Decimal, kind: str, views: int | None) -> None:
        assert deal.proof is not None
        terms = self._terms(deal)
        att = self.ctx.attestor.attest(deal.id, deal.proof.video_id, terms.payee, amount, kind,  # type: ignore[arg-type]
                                       deal.proof.checks, views=views)  # fmt: skip
        async with self._lock:
            tx = await asyncio.to_thread(self.ctx.escrow.release, deal.id, to_token_units(amount, self.ctx.token.decimals), att.nonce, att.signature)
        deal.attestations.append(att)
        deal.payouts.append(Payout(kind=kind, to=terms.payee, amount=amount, tx_hash=tx, at=self.ctx.clock.now()))  # type: ignore[arg-type]
        await self.audit(deal.campaign_id, deal.id, "escrow", f"{kind}_released", amount=amount, to=terms.payee, tx=tx,
                         attestation=att.evidence_hash, views=views)  # fmt: skip
        await self.notify_influencer(deal, "payout", kind=kind, amount=amount, tx=tx)

    async def _release_fixed(self, deal: Deal) -> None:
        still_ok, _ = await self._recheck(deal)
        if not still_ok:
            await self.audit(deal.campaign_id, deal.id, "verifier", "post_removed_before_release")
            await self.save(deal, DealState.PROOF_REJECTED)
            return
        await self._release(deal, self._terms(deal).fixed_fee, "fixed", None)
        await self.save(deal, DealState.FIXED_RELEASED)

    async def _release_performance(self, deal: Deal) -> None:
        still_ok, views = await self._recheck(deal)
        terms = self._terms(deal)
        amount = performance_payout(terms, views) if still_ok else Decimal("0")
        if amount > 0:
            await self._release(deal, amount, "performance", views)
        async with self._lock:
            tx, refunded_units = await asyncio.to_thread(self.ctx.escrow.close, deal.id)
        refund = Decimal(refunded_units) / (Decimal(10) ** self.ctx.token.decimals)
        if refund > 0:
            deal.payouts.append(Payout(kind="refund", to=self.ctx.advertiser_address, amount=refund, tx_hash=tx, at=self.ctx.clock.now()))
        await self.audit(deal.campaign_id, deal.id, "escrow", "deal_closed", views=views, performance=amount, refunded=refund, tx=tx)
        await self.save(deal, DealState.COMPLETED)

    async def _refund_expired(self, deal: Deal) -> None:
        async with self._lock:
            tx, units = await asyncio.to_thread(self.ctx.escrow.refund_expired, deal.id, int(self.ctx.clock.now().timestamp()))
        refund = Decimal(units) / (Decimal(10) ** self.ctx.token.decimals)
        deal.payouts.append(Payout(kind="refund", to=self.ctx.advertiser_address, amount=refund, tx_hash=tx, at=self.ctx.clock.now()))
        await self.audit(deal.campaign_id, deal.id, "escrow", "expired_refunded", refunded=refund, tx=tx)
        await self.save(deal, DealState.EXPIRED_REFUNDED)

    @staticmethod
    def _terms(deal: Deal) -> DealTerms:
        if deal.terms is None:
            raise DealError(f"deal {deal.id} has no agreed terms")
        return deal.terms
