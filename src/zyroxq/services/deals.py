"""Deal lifecycle after negotiation: Cart Mandate → policy gate → Payment Mandate (signed in the
advertiser's browser wallet) → HSP settlement → escrow funding → proof verification → payouts.
Every transition is written to the audit log and pushed to the participants' live streams."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx

from ..agent_policy import AdvertiserPolicy, DecisionRecord, calculate
from ..chain.escrow import EscrowError
from ..chain.settlement import FundingEvidence, SettlementMismatch, SettlementPending
from ..compliance import proof_checks as disclosure_checks
from ..hsp.cart import build_cart_contents, cart_method, cart_total
from ..hsp.webhook import verify_webhook_signature
from ..models import (
    Approval,
    AuditEvent,
    Campaign,
    CampaignSpec,
    Deal,
    DealState,
    DealTerms,
    InfluencerProfile,
    Offer,
    PaymentAttempt,
    Payout,
    Proof,
    ProofCheck,
    transition_allowed,
)
from ..policy import PolicyResult, check_cart, check_terms, performance_payout
from ..verifier.verify import all_passed, check_video
from ..verifier.youtube import parse_video_id
from ..wallet.gateway import GatewayRejected, GatewayUnavailable
from ..wallet.signer import mandate_from_signature, payment_authorization, payment_typed_data, recover_payment_mandate_signer, to_token_units
from .context import AppContext

log = logging.getLogger(__name__)

# Payment submitted or in flight, no verified settlement yet
PAYMENT_PENDING = {DealState.AWAITING_CHECKOUT, DealState.SIGNED, DealState.FUNDING, DealState.PAYMENT_UNKNOWN}
# Entering these frees the deal's budget reservation: the authorization can no longer be (or never was) paid
RELEASES_BUDGET = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_FAILED, DealState.PAYMENT_EXPIRED, DealState.EXPIRED_REFUNDED}
AUTH_EXPIRED = "authorization expired or expires too soon to submit"
POLICY_KIND = "agent_policy"
# Audit kinds only the advertiser sees: they name the advertiser's limits or internal reasoning
ADVERTISER_PRIVATE_EVENTS = {"negotiation_move", "negotiation_decision", "policy_gate", "approval_decision", "campaign_full",
                             "discovered", "agent_unreachable", "authorization_redrafted"}  # fmt: skip


class DealError(Exception):
    pass


class DealService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = asyncio.Lock()  # serializes escrow/ledger mutations
        self._owners: dict[str, str] = {}  # campaign → advertiser user, for live-event routing
        self._deal_influencers: dict[str, str] = {}
        self._deal_locks: dict[str, asyncio.Lock] = {}

    # ---- helpers -----------------------------------------------------------

    async def audit(self, campaign_id: str, deal_id: str | None, actor: str, kind: str, /, **detail: Any) -> None:
        event = AuditEvent(at=self.ctx.clock.now(), campaign_id=campaign_id, deal_id=deal_id, actor=actor, kind=kind,
                           detail=json.loads(json.dumps(detail, default=str)))  # fmt: skip
        await self.ctx.store.add_event(event)
        await self._publish({"type": "audit", "campaign_id": campaign_id, "deal_id": deal_id, "actor": actor, "kind": kind}, campaign_id, deal_id)

    async def save(self, deal: Deal, state: DealState | None = None) -> Deal:
        if state is not None:
            stored = await self.ctx.store.get_deal(deal.id)
            if stored is not None and not transition_allowed(stored.state, state):
                raise DealError(f"deal {deal.id}: {stored.state} → {state} is not allowed")
            deal.state = state
        deal.updated_at = self.ctx.clock.now()
        await self.ctx.store.save_deal(deal)
        if deal.state in RELEASES_BUDGET:
            await self.ctx.store.release_budget(deal.id)
        self._deal_influencers[deal.id] = deal.influencer_id
        await self._publish({"type": "deal", "campaign_id": deal.campaign_id, "deal_id": deal.id, "state": deal.state}, deal.campaign_id, deal.id)
        return deal

    async def save_campaign(self, campaign: Campaign) -> None:
        await self.ctx.store.save_campaign(campaign)
        self._owners[campaign.spec.id] = campaign.spec.owner_id
        await self._publish({"type": "campaign", "campaign_id": campaign.spec.id, "status": campaign.status}, campaign.spec.id, None)

    async def _publish(self, event: dict[str, Any], campaign_id: str, deal_id: str | None) -> None:
        if campaign_id not in self._owners and (c := await self.ctx.store.get_campaign(campaign_id)):
            self._owners[campaign_id] = c.spec.owner_id
        audience = {self._owners.get(campaign_id, "")}
        if deal_id:
            if deal_id not in self._deal_influencers and (d := await self.ctx.store.get_deal(deal_id)):
                self._deal_influencers[deal_id] = d.influencer_id
            audience.add(self._deal_influencers.get(deal_id, ""))
        audience.discard("")
        self.ctx.bus.publish(event, audience)

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
        """Caps reserved from the campaign budget by deals that are being, or were, paid."""
        return await self.ctx.store.reserved_budget(campaign_id, exclude=exclude)

    async def profile(self, influencer_id: str) -> InfluencerProfile:
        reg = await self.ctx.registry.get(influencer_id)
        if reg is None:
            raise DealError(f"influencer {influencer_id} is no longer active")
        return reg.profile

    async def notify_influencer(self, deal: Deal, message_type: str, /, **data: Any) -> None:
        """Tell the influencer's agent over A2A. Best effort: a down agent never blocks settlement."""
        reg = await self.ctx.registry.get(deal.influencer_id)
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
        terminal = {DealState.REJECTED, DealState.BLOCKED, DealState.PAYMENT_FAILED, DealState.PAYMENT_EXPIRED, DealState.COMPLETED,
                    DealState.EXPIRED_REFUNDED}  # fmt: skip
        if DealState.AWAITING_APPROVAL in states:
            status = "awaiting_approval"
        elif states & (PAYMENT_PENDING | {DealState.CART_CREATED}):
            status = "funding"
        elif states and states <= terminal:
            status = "done"
        else:
            status = "active"
        if status != c.status:
            c.status = status  # type: ignore[assignment]
            await self.save_campaign(c)

    # ---- negotiation bookkeeping ------------------------------------------

    async def advertiser_policy(self, spec: CampaignSpec) -> AdvertiserPolicy:
        """The advertiser policy version the campaign snapshotted at creation (defaults when it had none)."""
        if spec.agent_policy_version and spec.owner_id:
            doc = await self.ctx.store.get_version(POLICY_KIND, spec.owner_id, spec.agent_policy_version)
            if doc and doc.get("role") == "advertiser":
                return AdvertiserPolicy.model_validate(doc)
        return AdvertiserPolicy()

    async def record_decision(self, record: DecisionRecord | dict[str, Any]) -> None:
        """Stores a decision record for its owner. Only the public explanation ever reaches the other party."""
        rec = record if isinstance(record, DecisionRecord) else DecisionRecord.model_validate(record)
        await self.ctx.store.add_decision(rec.model_dump(mode="json"))
        actor = "advertiser-agent" if rec.party == "advertiser" else f"influencer-agent:{rec.actor_id}"
        kind = "negotiation_decision" if rec.party == "advertiser" else "influencer_decision"
        await self.audit(rec.campaign_id, rec.deal_id, actor, kind, decision_id=rec.id, action=rec.action, round=rec.round,
                         decided_by=rec.decided_by, policy_version=rec.policy_version)  # fmt: skip

    async def end_negotiation(self, deal: Deal, state: DealState, outcome: str) -> Deal:
        deal.negotiation_outcome = outcome
        return await self.save(deal, state)

    async def record_approval(self, deal_id: str, kind: str, by: str, approved: bool, note: str = "") -> Deal:
        deal = await self.get(deal_id)
        terms = self._terms(deal)
        deal.approvals.append(Approval(kind=kind, by=by, approved=approved, terms_hash=terms.terms_hash(), note=note, at=self.ctx.clock.now()))  # type: ignore[arg-type]
        return await self.save(deal)

    def negotiation_view(self, deal: Deal, spec: CampaignSpec) -> dict[str, Any]:
        """The Negotiation Room: structured offers, what moved between rounds, the payable breakdown and the outcome.
        Built from what both parties already exchanged; no private limits or decision records."""

        def amounts(o: dict[str, Any] | None) -> dict[str, str] | None:
            return {k: o[k] for k in ("fixed_fee", "cpm", "cap")} if o else None

        def delta(a: dict[str, str] | None, b: dict[str, str] | None) -> dict[str, str] | None:
            if not a or not b:
                return None
            return {k: str(Decimal(b[k]) - Decimal(a[k])) for k in ("fixed_fee", "cpm")}

        rounds, prev_offer, prev_counter = [], None, None
        for r in deal.rounds:
            offer, reply = amounts(r["offer"]), r["reply"]
            counter = amounts(reply.get("counter")) if reply.get("kind") == "counter" else None
            rounds.append({
                "round": r["offer"]["round"],
                "advertiser_offer": offer,
                "advertiser_moved": delta(prev_offer, offer),
                "reply": reply.get("kind"),
                "explanation": reply.get("reason", ""),
                "counter": counter,
                "creator_moved": delta(prev_counter, counter),
            })  # fmt: skip
            prev_offer, prev_counter = offer, counter or prev_counter
        latest = deal.terms or (deal.rounds[-1]["offer"] if deal.rounds else None)
        breakdown = None
        if latest is not None:
            t = latest if isinstance(latest, dict) else latest.model_dump(mode="json")
            fixed, cpm, cap = Decimal(t["fixed_fee"]), Decimal(t["cpm"]), Decimal(t["cap"])
            model = t.get("compensation_model", spec.compensation_model)
            views_to_cap = int(((cap - fixed) / cpm * 1000).to_integral_value(rounding="ROUND_CEILING")) if cpm > 0 and cap > fixed else None
            samples = sorted({0, 10_000, *([views_to_cap] if views_to_cap else [])})
            breakdown = {"model": model, "views_to_cap": views_to_cap,
                         "examples": [calculate(model, fixed, cpm, v, cap).as_dict() for v in samples]}  # fmt: skip
        status = {DealState.NEGOTIATING: "open", DealState.REJECTED: "failed", DealState.ESCALATED: "escalated"}.get(deal.state, "agreed")
        if deal.state == DealState.REJECTED and deal.terms is not None:
            status = "agreed_then_cancelled"
        return {
            "deal_id": deal.id,
            "status": status,
            "outcome": deal.negotiation_outcome,
            "max_rounds": spec.max_rounds,
            "rounds_used": len(deal.rounds),
            "rounds_remaining": max(0, spec.max_rounds - len(deal.rounds)) if deal.state == DealState.NEGOTIATING else 0,
            "compensation_model": spec.compensation_model,
            "scope": spec.scope.model_dump(),
            "rounds": rounds,
            "payable": breakdown,
            "agreement": {
                "terms": deal.terms.model_dump(mode="json"),
                "terms_hash": deal.terms.terms_hash(),
                "approved_terms_hash": deal.approved_terms_hash,
                "approvals": [a.model_dump(mode="json") for a in deal.approvals],
            }
            if deal.terms
            else None,  # fmt: skip
        }

    async def compliance_view(self, spec: CampaignSpec) -> dict[str, Any]:
        """Campaign Compliance & Audit: which disclosure policy versions apply, and each deal's gates."""
        from ..compliance import DISCLAIMER

        deals = []
        for d in await self.ctx.store.list_deals(spec.id):
            if d.terms is None:
                continue
            checks = [c.model_dump() for c in (d.proof.checks if d.proof else []) if c.name.startswith("disclosure")]
            h = d.terms.terms_hash()
            review = next((a for a in reversed(d.approvals) if a.kind == "compliance_review" and a.terms_hash == h), None)
            payment = next((a for a in reversed(d.approvals) if a.kind == "payment" and a.terms_hash == h), None)
            gates = []
            if d.proof is None:
                gates.append("waiting for the post")
            elif not all(c["passed"] for c in checks):
                gates.append("disclosure checks failed")
            if self.review_pending(d, spec) and d.state == DealState.VERIFIED:
                gates.append("advertiser disclosure review pending")
            deals.append({
                "deal_id": d.id,
                "influencer_id": d.influencer_id,
                "state": d.state,
                "disclosure_policies": d.terms.disclosure_policies,
                "checks": checks,
                "attestations": d.proof.attestations if d.proof else {},
                "review": "not_required" if not spec.review_required else (("approved" if review.approved else "rejected") if review else "pending"),
                "payment_approval": payment.model_dump(mode="json") if payment else None,
                "terms_hash": h,
                "terms_hash_matches_approval": d.approved_terms_hash in (None, h),
                "gates": gates,
                "funding_simulated": d.funding_simulated,
            })  # fmt: skip
        return {
            "campaign_id": spec.id,
            "jurisdictions": spec.jurisdictions,
            "required_tags": spec.required_tags,
            "review_required": spec.review_required,
            "policies": [p.model_dump(mode="json") for p in self.ctx.disclosure.resolve_all(spec.disclosure_policies)],
            "disclaimer": DISCLAIMER,
            "settlement_simulated": self.ctx.gateway.simulated or self.ctx.escrow.simulated,
            "deals": deals,
        }

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
            # A fixed deal has no performance line, a CPM deal no fixed line: HSP rejects zero amounts
            items=[(n, a) for n, a in (("Fixed fee", terms.fixed_fee), ("Performance fee (max)", terms.cap - terms.fixed_fee)) if a > 0],
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
        """Everything that must hold before the wallet may sign. Failure → BLOCKED, nothing signed.
        Passing reserves the deal's cap from the campaign budget atomically (store.reserve_budget)."""
        terms = self._terms(deal)
        assert deal.cart_contents is not None
        result = check_terms(spec, terms, await self.profile(deal.influencer_id), agreed)
        cart = check_cart(spec, terms, deal.cart_contents, self.ctx.token, self.ctx.escrow.address,
                          await self.committed_spend(deal.campaign_id, exclude=deal.id), self.ctx.clock.now())  # fmt: skip
        result.violations += cart.violations
        if result.ok and not await self.ctx.store.reserve_budget(spec.id, deal.id, terms.cap, spec.budget):
            result.violations.append(f"budget exceeded: another deal reserved the remaining budget of {spec.budget}")
        await self.audit(deal.campaign_id, deal.id, "policy", "policy_gate", ok=result.ok, violations=result.violations)
        if not result.ok:
            deal.policy_violations = result.violations
            await self.save(deal, DealState.BLOCKED)
        return result

    async def await_signature(self, deal: Deal, payer: str) -> Deal:
        """Drafts the EIP-3009 authorization the advertiser's wallet signs (simulated gateway; hosted mode
        signs in HSP checkout). The draft is fixed server-side, so the browser can only sign it, not edit it."""
        assert deal.cart_contents is not None
        deal.payer = payer
        deal.approved_terms_hash = self._terms(deal).terms_hash()  # the authorization pays for exactly these terms
        if self.ctx.gateway.agent_signing:
            now = int(self.ctx.clock.now().timestamp())
            deal.mandate_draft = payment_authorization(payer, deal.cart_contents, self.ctx.token, now - 60, now + self.ctx.settings.authorization_ttl_seconds)
        return await self.save(deal, DealState.AWAITING_APPROVAL)

    async def redraft(self, deal: Deal) -> Deal:
        """Replaces an unsigned authorization that expired (or is about to) with a fresh one: new window, new nonce."""
        if deal.state != DealState.AWAITING_APPROVAL or not self.ctx.gateway.agent_signing or deal.payer is None:
            raise DealError(f"deal is {deal.state}, nothing to redraft")
        await self.audit(deal.campaign_id, deal.id, "advertiser-agent", "authorization_redrafted")
        return await self.await_signature(deal, deal.payer)

    def typed_data(self, deal: Deal) -> dict[str, Any] | None:
        return payment_typed_data(deal.mandate_draft, self.ctx.token) if deal.mandate_draft else None

    def signature_valid(self, deal: Deal, signature: str) -> bool:
        if not (deal.mandate_draft and deal.cart_contents and deal.payment_request_id and deal.payer):
            return False
        mandate = mandate_from_signature(deal.mandate_draft, signature, deal.cart_contents, deal.payment_request_id, self.ctx.token)
        return recover_payment_mandate_signer(mandate, self.ctx.token).lower() == deal.payer.lower()

    async def authorization_problems(self, deal: Deal) -> list[str]:
        """Server-side checks on the drafted authorization before it may be signed or submitted. Nothing here
        trusts the browser: amount, recipient, token, chain, payer and budget come from server state."""
        problems: list[str] = []
        if deal.state != DealState.AWAITING_APPROVAL:
            return [f"deal is {deal.state}, not awaiting a signature"]
        terms, auth, contents = deal.terms, deal.mandate_draft, deal.cart_contents
        if terms is None or auth is None or contents is None or deal.payer is None:
            return ["no authorization drafted for this deal"]
        spec = (await self.campaign(deal.campaign_id)).spec
        method = cart_method(contents)
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        now = int(self.ctx.clock.now().timestamp())
        checks = [
            (contents["id"] == deal.id, "cart mandate belongs to another deal"),
            (method["chain_id"] == self.ctx.token.chain_id == self.ctx.escrow.chain_id, "wrong chain"),
            (method["contract_address"].lower() == self.ctx.token.contract_address.lower(), "wrong token contract"),
            (auth["to"].lower() == method["pay_to"].lower() == self.ctx.escrow.address.lower(), "recipient is not the escrow"),
            (int(auth["value"]) == cap_units == to_token_units(cart_total(contents), self.ctx.token.decimals), "amount differs from the agreed cap"),
            (auth["from"].lower() == deal.payer.lower() == spec.payer.lower(), "payer is not the campaign's wallet"),
            (int(auth["validAfter"]) <= now, "authorization is not valid yet"),
            (int(auth["validBefore"]) - now >= self.ctx.settings.authorization_min_remaining_seconds, AUTH_EXPIRED),
            (deal.approved_terms_hash == terms.terms_hash(), "terms changed after the authorization was drafted"),
        ]
        problems += [msg for ok, msg in checks if not ok]
        if not await self.ctx.store.reserve_budget(spec.id, deal.id, terms.cap, spec.budget):
            problems.append("budget exceeded")
        return problems

    async def funding_summary(self, campaign_id: str) -> dict[str, str]:
        """Budget is a spending limit, not money held: reserved = caps of deals that may still be paid or were paid."""
        spec = (await self.campaign(campaign_id)).spec
        reserved = await self.ctx.store.reserved_budget(campaign_id)
        funded = sum((d.terms.cap for d in await self.ctx.store.list_deals(campaign_id) if d.funding_tx and d.terms), Decimal("0"))
        return {"budget": str(spec.budget), "reserved": str(reserved), "funded": str(funded), "remaining": str(spec.budget - reserved)}

    async def submit_payment(self, deal: Deal, signature: str | None) -> Deal:
        """Fixed code path after the policy gate and the advertiser's wallet signature."""
        assert deal.cart_contents is not None and deal.payment_request_id is not None
        if not self.ctx.gateway.agent_signing:
            await self.save(deal, DealState.AWAITING_CHECKOUT)
            await self.audit(deal.campaign_id, deal.id, "advertiser-agent", "awaiting_hosted_checkout", payment_url=deal.payment_url)
            return deal
        if deal.state != DealState.AWAITING_APPROVAL:
            raise DealError(f"deal {deal.id} is {deal.state}: its payment was already submitted or closed")
        problems = await self.authorization_problems(deal)
        if problems:
            await self.save(deal, DealState.PAYMENT_EXPIRED if problems == [AUTH_EXPIRED] else DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "authorization_refused", problems=problems)
            return deal
        if signature is None or not self.signature_valid(deal, signature):
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "payment_signature_invalid", payer=deal.payer)
            return deal
        assert deal.mandate_draft is not None
        mandate = mandate_from_signature(deal.mandate_draft, signature, deal.cart_contents, deal.payment_request_id, self.ctx.token)
        auth = mandate.authorization
        if await self.ctx.store.claim("auth_nonce", auth["nonce"], deal.id) != deal.id:
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "policy", "authorization_replayed", nonce=auth["nonce"][:10] + "…")
            return deal

        # Persist the attempt before submitting, so a crash or timeout leaves a record to reconcile
        now = self.ctx.clock.now()
        deal.payment_attempts.append(PaymentAttempt(idempotency_key=auth["nonce"], payer=mandate.payer, to=auth["to"], value_units=int(auth["value"]),
                                                    valid_before=int(auth["validBefore"]), gateway=self.ctx.gateway.name,
                                                    simulated=self.ctx.gateway.simulated, created_at=now, updated_at=now))  # fmt: skip
        await self.save(deal, DealState.SIGNED)
        await self.audit(deal.campaign_id, deal.id, "wallet", "payment_mandate_signed", payer=mandate.payer, value=auth["value"], to=auth["to"],
                         scheme="x402 exact / EIP-3009", simulated=self.ctx.gateway.simulated)  # fmt: skip
        try:
            await asyncio.wait_for(self.ctx.gateway.submit_payment_mandate(mandate), self.ctx.settings.hsp_submit_timeout_seconds)
        except GatewayRejected as e:
            deal = await self._attempt(deal.id, auth["nonce"], "failed", error=str(e))
            await self.save(deal, DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_rejected", reason=str(e))
        except (TimeoutError, GatewayUnavailable, httpx.HTTPError) as e:
            # The transfer may still settle: never fail it or re-sign, reconcile with HSP and the chain instead
            deal = await self._attempt(deal.id, auth["nonce"], "unknown", error=f"{type(e).__name__}: {e}"[:300])
            if deal.state in (DealState.SIGNED, DealState.FUNDING):
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_outcome_unknown", reason=type(e).__name__)
        else:
            await self._attempt(deal.id, auth["nonce"], "pending")
        return await self.get(deal.id)

    async def _attempt(self, deal_id: str, key: str, status: str, *, tx_hash: str | None = None, error: str = "") -> Deal:
        """Updates a payment attempt. `pending` never overwrites an outcome a webhook already recorded."""
        deal = await self.get(deal_id)
        for a in deal.payment_attempts:
            if a.idempotency_key == key and not (status == "pending" and a.status != "submitted"):
                a.status = status  # type: ignore[assignment]
                a.tx_hash = tx_hash or a.tx_hash
                a.error = error or a.error
                a.updated_at = self.ctx.clock.now()
        return await self.save(deal)

    # ---- HSP notifications + settlement verification ------------------------------

    async def handle_webhook(self, raw: bytes, signature_header: str | None) -> bool:
        now = int(self.ctx.clock.now().timestamp())
        if not verify_webhook_signature(signature_header, raw, self.ctx.gateway.webhook_secret, now=now):
            return False
        payload = json.loads(raw)
        await self.on_payment(payload)
        # Recorded after processing, so a crash mid-way lets HSP's retry through; replays are harmless
        # because every transition below is idempotent on the deal's state and funding transaction.
        await self.ctx.store.claim_webhook(str(payload.get("request_id", "")), str(payload.get("status", "")))
        return True

    async def on_payment(self, p: dict[str, Any]) -> None:
        """An HSP payment notification. It is a claim, not evidence: FUNDED needs a verified settlement."""
        deal = await self.ctx.store.get_deal(str(p.get("cart_mandate_id", "")))
        if deal is None or deal.terms is None:
            log.warning("payment notification for unknown deal %s", p.get("cart_mandate_id"))
            return
        claimed = (Decimal(str(p.get("order_amount", "0"))), str(p.get("to_pay_address", "")).lower(), p.get("token"))
        if claimed != (deal.terms.cap, self.ctx.escrow.address.lower(), deal.terms.asset):
            await self.audit(deal.campaign_id, deal.id, "policy", "notification_mismatch", amount=p.get("order_amount"), to=p.get("to_pay_address"),
                             token=p.get("token"))  # fmt: skip
            if deal.state in PAYMENT_PENDING:
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.refresh_campaign_status(deal.campaign_id)
            return
        await self._apply_status(deal, str(p.get("status", "")), p.get("tx_signature"), p.get("payer_address"), source="webhook")

    async def reconcile(self, deal: Deal) -> None:
        """Asks HSP again and re-checks the chain for a deal whose payment is pending or ambiguous."""
        if deal.payment_request_id is None:
            return
        try:
            lookup = await self.ctx.gateway.lookup_payment(deal.payment_request_id)
        except GatewayUnavailable as e:
            await self.audit(deal.campaign_id, deal.id, "hsp", "reconcile_unavailable", reason=str(e)[:200])
            return
        if lookup is not None:
            await self._apply_status(deal, lookup.status, lookup.tx_hash, lookup.payer, source="reconcile")
            return
        # HSP has no record. Only the token contract can say the authorization will never settle.
        attempt = deal.payment_attempts[-1] if deal.payment_attempts else None
        if attempt is None or int(self.ctx.clock.now().timestamp()) < attempt.valid_before:
            return
        try:
            used = await asyncio.to_thread(self.ctx.settlement.authorization_used, attempt.payer, attempt.idempotency_key)
        except SettlementPending as e:
            await self.audit(deal.campaign_id, deal.id, "escrow", "reconcile_inconclusive", reason=str(e))
            return
        if not used:
            await self._attempt(deal.id, attempt.idempotency_key, "failed", error="authorization expired unused")
            await self.save(await self.get(deal.id), DealState.PAYMENT_FAILED)
            await self.audit(deal.campaign_id, deal.id, "escrow", "authorization_expired_unused")

    async def _apply_status(self, deal: Deal, status: str, tx_hash: Any, payer: Any, *, source: str) -> None:
        async with self._deal_lock(deal.id):
            deal = await self.get(deal.id)
            await self.audit(deal.campaign_id, deal.id, "hsp", "payment_status", status=status, tx=tx_hash, source=source)
            if deal.state not in PAYMENT_PENDING:
                return  # already settled or closed: duplicate or late notification
            deal.payment_status = status
            if status == "payment-failed":
                await self.save(deal, DealState.PAYMENT_FAILED)
            elif status in ("payment-included", "payment-safe"):
                await self.save(deal, DealState.FUNDING)
            elif status == "payment-finalized":
                await self._settle(deal, tx_hash if isinstance(tx_hash, str) else None, payer if isinstance(payer, str) else None)
            else:
                await self.save(deal)
        await self.refresh_campaign_status(deal.campaign_id)

    async def _settle(self, deal: Deal, tx_hash: str | None, payer: str | None) -> None:
        """HSP says finalized. Verify the transaction, bind it to this deal once, then lock it in escrow."""
        terms = self._terms(deal)
        expected_payer = deal.payer or payer
        if tx_hash is None or expected_payer is None or (payer and payer.lower() != expected_payer.lower()):
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_unverified", reason="no transaction hash or unexpected payer")
            return
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        try:
            evidence = await asyncio.to_thread(self.ctx.settlement.verify_funding, tx_hash, expected_payer, self.ctx.escrow.address, cap_units)
        except SettlementPending as e:
            await self.save(deal, DealState.FUNDING)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_pending", tx=tx_hash, reason=str(e))
            return
        except (SettlementMismatch, EscrowError) as e:
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_unverified", tx=tx_hash, reason=str(e))
            return
        except Exception as e:  # noqa: BLE001 - RPC unreachable: keep pending, reconcile retries
            await self.save(deal, DealState.FUNDING)
            await self.audit(deal.campaign_id, deal.id, "escrow", "settlement_check_failed", tx=tx_hash, reason=type(e).__name__)
            return
        if await self.ctx.store.claim("funding_tx", tx_hash, deal.id) != deal.id:
            await self.save(deal, DealState.PAYMENT_UNKNOWN)
            await self.audit(deal.campaign_id, deal.id, "escrow", "funding_tx_reused", tx=tx_hash)
            return
        await self._fund_escrow(deal, evidence)

    async def _fund_escrow(self, deal: Deal, evidence: FundingEvidence) -> None:
        terms = self._terms(deal)
        cap_units = to_token_units(terms.cap, self.ctx.token.decimals)
        async with self._lock:
            try:
                on_chain = await asyncio.to_thread(self.ctx.escrow.get_deal, deal.id)
                if on_chain.get("state", "None") == "None":
                    await asyncio.to_thread(self.ctx.escrow.register_deal, deal.id, evidence.payer, terms.payee, cap_units,
                                            int(terms.publish_by.timestamp()))  # fmt: skip
                    on_chain = {"state": "Registered"}
                if on_chain["state"] == "Registered":
                    await asyncio.to_thread(self.ctx.escrow.mark_funded, deal.id, evidence.tx_hash)
                elif on_chain["state"] != "Funded" or on_chain.get("funding_tx", "").lower() != evidence.tx_hash.lower():
                    raise EscrowError(f"escrow deal is {on_chain['state']}")
            except EscrowError as e:
                # The money is verifiably in the escrow but could not be bound: operator attention, never a re-payment
                await self.save(deal, DealState.PAYMENT_UNKNOWN)
                await self.audit(deal.campaign_id, deal.id, "escrow", "funding_failed", reason=str(e))
                return
        deal.funding_tx = evidence.tx_hash
        deal.funding_simulated = evidence.simulated
        await self.save(deal, DealState.FUNDED)
        if deal.payment_attempts:
            await self._attempt(deal.id, deal.payment_attempts[-1].idempotency_key, "succeeded", tx_hash=evidence.tx_hash)
        await self.audit(deal.campaign_id, deal.id, "escrow", "escrow_funded", cap=terms.cap, escrow=self.ctx.escrow.address, tx=evidence.tx_hash,
                         confirmations=evidence.confirmations, simulated=evidence.simulated)  # fmt: skip
        await self.notify_influencer(deal, "funded", amount_locked=terms.cap, escrow=self.ctx.escrow.address, tx=evidence.tx_hash,
                                     simulated=evidence.simulated)  # fmt: skip

    def _deal_lock(self, deal_id: str) -> asyncio.Lock:
        return self._deal_locks.setdefault(deal_id, asyncio.Lock())

    # ---- proof + payouts -----------------------------------------------------

    async def submit_proof(self, deal_id: str, video_ref: str, attestations: dict[str, bool] | None = None) -> Deal:
        deal = await self.get(deal_id)
        if deal.state not in (DealState.FUNDED, DealState.PROOF_REJECTED):
            raise DealError(f"deal is {deal.state}, not awaiting proof")
        video_id = parse_video_id(video_ref)
        if video_id is None:
            raise DealError("not a YouTube URL or video id")
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
        spec = (await self.campaign(deal.campaign_id)).spec
        video = await self.ctx.videos.get_video(video_id)
        assert deal.agreed_at is not None
        terms = self._terms(deal)
        checks = check_video(terms, video, deal.agreed_at, spec.brand_handle)
        if video is not None:
            checks += self._disclosure_checks(terms, video, attestations or {})
        now = self.ctx.clock.now()
        deal.proof = Proof(video_id=video_id, submitted_at=now, checks=checks, attestations=attestations or {},
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

    def _disclosure_checks(self, terms: DealTerms, video: Any, attestations: dict[str, bool]) -> list[ProofCheck]:
        policies = self.ctx.disclosure.resolve_all(terms.disclosure_policies)
        unknown = [i for i in terms.disclosure_policies if self.ctx.disclosure.resolve(i) is None]
        checks = disclosure_checks(policies, video, attestations)
        if unknown:
            checks.append(ProofCheck(name="disclosure_policy_known", passed=False, detail=f"unknown disclosure policy {unknown}"))
        return checks

    async def compliance_review(self, deal_id: str, reviewer: str, approved: bool, note: str) -> Deal:
        """The advertiser's disclosure review of a verified post. Approval unblocks the fixed fee; rejection sends
        the proof back, and the creator can resubmit until the publish deadline."""
        deal = await self.get(deal_id)
        if deal.state != DealState.VERIFIED:
            raise DealError(f"deal is {deal.state}: only verified posts can be reviewed")
        deal = await self.record_approval(deal_id, "compliance_review", reviewer, approved, note)
        await self.audit(deal.campaign_id, deal.id, "human", "compliance_reviewed", approved=approved, note=note)
        if not approved:
            deal = await self.save(deal, DealState.PROOF_REJECTED)
        return deal

    def review_pending(self, deal: Deal, spec: CampaignSpec) -> bool:
        if not spec.review_required or deal.terms is None:
            return False
        h = deal.terms.terms_hash()
        return not any(a.kind == "compliance_review" and a.approved and a.terms_hash == h for a in deal.approvals)

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
            if deal.state in PAYMENT_PENDING:
                await self.reconcile(deal)
                continue
            try:
                if deal.state in (DealState.FUNDED, DealState.PROOF_REJECTED) and now > self._terms(deal).publish_by:
                    await self._refund_expired(deal)
                elif deal.state == DealState.VERIFIED and deal.fixed_release_due and now >= deal.fixed_release_due:
                    if self.review_pending(deal, (await self.campaign(deal.campaign_id)).spec):
                        continue  # the advertiser's disclosure review gates the release
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
        if self.ctx.videos is None:
            raise DealError("the verifier is offline: set YOUTUBE_API_KEY")
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
        deal.payouts.append(Payout(kind=kind, to=terms.payee, amount=amount, tx_hash=tx, at=self.ctx.clock.now(),  # type: ignore[arg-type]
                                   simulated=self.ctx.escrow.simulated))  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, "escrow", f"{kind}_released", amount=amount, to=terms.payee, tx=tx,
                         attestation=att.evidence_hash, views=views, simulated=self.ctx.escrow.simulated)  # fmt: skip
        await self.notify_influencer(deal, "payout", kind=kind, amount=amount, tx=tx)

    async def _release_fixed(self, deal: Deal) -> None:
        still_ok, _ = await self._recheck(deal)
        if not still_ok:
            await self.audit(deal.campaign_id, deal.id, "verifier", "post_removed_before_release")
            await self.save(deal, DealState.PROOF_REJECTED)
            return
        if self._terms(deal).fixed_fee > 0:  # CPM-only deals have no fixed fee to release
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
            deal.payouts.append(Payout(kind="refund", to=deal.payer or "", amount=refund, tx_hash=tx, at=self.ctx.clock.now(),
                                       simulated=self.ctx.escrow.simulated))  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, "escrow", "deal_closed", views=views, performance=amount, refunded=refund, tx=tx)
        await self.save(deal, DealState.COMPLETED)

    async def _refund_expired(self, deal: Deal) -> None:
        async with self._lock:
            tx, units = await asyncio.to_thread(self.ctx.escrow.refund_expired, deal.id, int(self.ctx.clock.now().timestamp()))
        refund = Decimal(units) / (Decimal(10) ** self.ctx.token.decimals)
        deal.payouts.append(Payout(kind="refund", to=deal.payer or "", amount=refund, tx_hash=tx, at=self.ctx.clock.now(),
                                   simulated=self.ctx.escrow.simulated))  # fmt: skip
        await self.audit(deal.campaign_id, deal.id, "escrow", "expired_refunded", refunded=refund, tx=tx)
        await self.save(deal, DealState.EXPIRED_REFUNDED)

    @staticmethod
    def _terms(deal: Deal) -> DealTerms:
        if deal.terms is None:
            raise DealError(f"deal {deal.id} has no agreed terms")
        return deal.terms
