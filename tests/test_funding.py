"""Funding: EIP-3009 authorization → HSP → settlement evidence → escrow. One test per failure mode;
the full happy path through the API (negotiation to payouts) is in test_e2e.py."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from eth_account import Account

from tests.conftest import ADVERTISER_KEY, INFLUENCER_KEY, VERIFIER_KEY
from zyroxq.chain.escrow import SIMULATED_TX_PREFIX, EscrowError, SimulatedEscrow
from zyroxq.chain.settlement import SettlementMismatch, find_transfer
from zyroxq.config import Settings, SettlementMode
from zyroxq.hsp.cart import cart_method, cart_total
from zyroxq.hsp.webhook import sign_webhook
from zyroxq.models import Campaign, Deal, DealState, DealTerms, Offer, transition_allowed
from zyroxq.services.context import build_context
from zyroxq.services.deals import DealError, DealService
from zyroxq.verifier.youtube import FixtureVideoSource, VideoInfo
from zyroxq.wallet.gateway import GatewayRejected, GatewayUnavailable, PaymentLookup, SimulatedGateway
from zyroxq.wallet.signer import sign_terms

PAYER = Account.from_key(ADVERTISER_KEY).address
CAP_UNITS = 40_000_000


def settings(**over) -> Settings:
    return Settings(_env_file=None, database_url="", anthropic_api_key="", youtube_api_key="", verifier_private_key=VERIFIER_KEY,
                    session_secret="s", **over)  # fmt: skip


@pytest.fixture
async def svc(campaign, influencer):
    ctx = await build_context(settings(hsp_submit_timeout_seconds=0.2))
    service = DealService(ctx)
    ctx.gateway.sink = service.handle_webhook
    service.profile = AsyncMock(return_value=influencer)  # registry stand-in: the influencer's verified profile
    spec = campaign.model_copy(update={"payer": PAYER, "owner_id": "USR-adv"})
    await service.save_campaign(Campaign(spec=spec, created_at=ctx.clock.now()))
    return service


async def new_deal(svc: DealService, influencer, n: int, *, gate: bool = True) -> Deal:
    """A deal through Cart Mandate and policy gate, ready for the advertiser's signature."""
    ctx = svc.ctx
    spec = (await svc.campaign("CMP-test")).spec
    did = f"DEAL-{n}"
    offer = Offer(deal_id=did, campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                  deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
    terms = sign_terms(INFLUENCER_KEY, DealTerms(deal_id=did, campaign_id=spec.id, advertiser=spec.advertiser, influencer_id=influencer.id,
                                                 channel_id=influencer.channel_id, payee=influencer.wallet, fixed_fee=offer.fixed_fee,
                                                 cpm=offer.cpm, cap=offer.cap, deliverable=offer.deliverable,
                                                 publish_by=ctx.clock.now() + timedelta(days=7), required_tags=["#ad"]))  # fmt: skip
    deal = await svc.save(Deal(id=did, campaign_id=spec.id, influencer_id=influencer.id, terms=terms, agreed_at=ctx.clock.now()), DealState.AGREED)
    deal = await svc.create_cart(deal)
    if not gate:
        return deal
    result = await svc.gate(deal, spec, offer)
    if not result.ok:
        return await svc.get(did)
    return await svc.await_signature(await svc.get(did), spec.payer)


def sign(svc: DealService, deal: Deal, key: str = ADVERTISER_KEY) -> str:
    """What MetaMask's eth_signTypedData_v4 returns for the server's draft."""
    typed = svc.typed_data(deal)
    assert typed is not None
    return "0x" + Account.sign_typed_data(key, full_message=typed).signature.hex().removeprefix("0x")


def events(svc: DealService) -> list[str]:
    return [e.kind for e in svc.ctx.store._events]  # type: ignore[attr-defined]


async def reserved(svc: DealService) -> Decimal:
    return await svc.ctx.store.reserved_budget("CMP-test")


# 1 -------------------------------------------------------------------------------------------


async def test_valid_authorization_funds_escrow_after_verified_settlement(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    assert deal.state == DealState.AWAITING_APPROVAL and await svc.authorization_problems(deal) == []
    deal = await svc.submit_payment(deal, sign(svc, deal))

    escrow: SimulatedEscrow = svc.ctx.escrow  # type: ignore[assignment]
    assert deal.state == DealState.FUNDED
    assert deal.funding_tx in escrow.settlements and escrow.get_deal(deal.id)["funding_tx"] == deal.funding_tx
    assert escrow.allocated == CAP_UNITS == escrow.balance
    assert [a.status for a in deal.payment_attempts] == ["succeeded"] and deal.payment_attempts[0].idempotency_key == deal.mandate_draft["nonce"]
    assert await reserved(svc) == Decimal("40")
    assert (await svc.funding_summary("CMP-test")) == {"budget": "120", "reserved": "40", "funded": "40", "remaining": "80"}


# 2 + 10 ---------------------------------------------------------------------------------------


class FailingTransferGateway(SimulatedGateway):
    """HSP accepts the mandate but the token transfer fails on chain (e.g. an empty wallet)."""

    async def submit_payment_mandate(self, mandate):
        contents = self._carts[mandate.payment_mandate_id]
        self._verify(mandate, contents)
        await self._emit(contents, mandate, "payment-failed", "0x" + "fa" * 32)
        return "0x" + "fa" * 32


def use_gateway(svc: DealService, cls: type[SimulatedGateway]) -> SimulatedGateway:
    old: SimulatedGateway = svc.ctx.gateway  # type: ignore[assignment]
    gw = cls(old.token, old.escrow, old.clock, old.base_url)
    gw._carts = old._carts
    gw.sink = svc.handle_webhook
    svc.ctx.gateway = gw
    return gw


async def test_empty_wallet_transfer_failure_never_funds(svc, influencer):
    use_gateway(svc, FailingTransferGateway)
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.state == DealState.PAYMENT_FAILED and deal.funding_tx is None
    assert svc.ctx.escrow.get_deal(deal.id) == {}  # never registered, never funded
    assert await reserved(svc) == 0  # budget freed for another creator
    with pytest.raises(DealError):
        await svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk")  # creator cannot start on an unfunded deal


async def test_hsp_rejection_fails_the_attempt(svc, influencer):
    class Rejecting(SimulatedGateway):
        async def submit_payment_mandate(self, mandate):
            raise GatewayRejected("10010 insufficient balance")

    use_gateway(svc, Rejecting)
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.state == DealState.PAYMENT_FAILED
    assert deal.payment_attempts[0].status == "failed" and "insufficient" in deal.payment_attempts[0].error
    assert "payment_rejected" in events(svc) and await reserved(svc) == 0


# 3 + 4 ---------------------------------------------------------------------------------------


async def test_rejected_in_wallet_means_nothing_is_submitted(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, None)  # the user clicked "Reject" in MetaMask: no signature exists
    assert deal.state == DealState.PAYMENT_FAILED and deal.payment_attempts == []
    assert svc.ctx.escrow.balance == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("signature", ["0x1234", "not-hex", "0x" + "00" * 65])
async def test_malformed_signature_is_refused(svc, influencer, signature):
    deal = await new_deal(svc, influencer, 1)
    assert not svc.signature_valid(deal, signature)
    assert (await svc.submit_payment(deal, signature)).state == DealState.PAYMENT_FAILED


async def test_signature_from_another_wallet_is_refused(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal, key="0x" + "55" * 32))
    assert deal.state == DealState.PAYMENT_FAILED and "payment_signature_invalid" in events(svc)
    assert svc.ctx.escrow.balance == 0  # type: ignore[attr-defined]


# 5 -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tamper, problem",
    [
        (lambda d: d.mandate_draft.update(to=Account.create().address), "recipient is not the escrow"),
        (lambda d: d.mandate_draft.update(value="1"), "amount differs"),
        (lambda d: d.mandate_draft.update({"from": Account.create().address}), "payer"),
        (lambda d: cart_method(d.cart_contents).update(chain_id=1), "wrong chain"),
        (lambda d: cart_method(d.cart_contents).update(contract_address="0x" + "12" * 20), "wrong token"),
    ],
)
async def test_wrong_chain_token_recipient_amount_or_payer(svc, influencer, tamper, problem):
    deal = await new_deal(svc, influencer, 1)
    tamper(deal)
    await svc.ctx.store.save_deal(deal)  # e.g. a corrupted or tampered record
    problems = await svc.authorization_problems(deal)
    assert any(problem in p for p in problems), problems
    deal = await svc.submit_payment(deal, "0x" + "ab" * 65)
    assert deal.state == DealState.PAYMENT_FAILED and "authorization_refused" in events(svc)
    assert svc.ctx.escrow.balance == 0  # type: ignore[attr-defined]


async def test_campaign_rejects_cart_for_wrong_escrow(svc, influencer):
    deal = await new_deal(svc, influencer, 1, gate=False)
    cart_method(deal.cart_contents)["pay_to"] = Account.create().address
    spec = (await svc.campaign("CMP-test")).spec
    offer = Offer(deal_id=deal.id, campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                  deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
    result = await svc.gate(deal, spec, offer)
    assert not result.ok and any("pay_to" in v for v in result.violations)
    assert await reserved(svc) == 0


# 6 -------------------------------------------------------------------------------------------


async def test_expired_authorization_is_never_submitted(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    signature = sign(svc, deal)
    svc.ctx.clock.advance(timedelta(hours=2))
    deal = await svc.submit_payment(deal, signature)
    assert deal.state == DealState.PAYMENT_EXPIRED and deal.payment_attempts == []
    assert await reserved(svc) == 0


async def test_expired_draft_can_be_redrafted_with_a_new_nonce(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    old_nonce = deal.mandate_draft["nonce"]
    svc.ctx.clock.advance(timedelta(hours=2))
    assert "expires too soon" in " ".join(await svc.authorization_problems(deal))
    deal = await svc.redraft(deal)
    assert deal.mandate_draft["nonce"] != old_nonce and await svc.authorization_problems(deal) == []
    assert (await svc.submit_payment(deal, sign(svc, deal))).state == DealState.FUNDED


# 7 -------------------------------------------------------------------------------------------


async def test_authorization_nonce_cannot_pay_twice(svc, influencer):
    a = await new_deal(svc, influencer, 1)
    b = await new_deal(svc, influencer, 2)
    b.mandate_draft["nonce"] = a.mandate_draft["nonce"]  # a draft that reuses another deal's authorization
    await svc.ctx.store.save_deal(b)
    assert (await svc.submit_payment(a, sign(svc, a))).state == DealState.FUNDED
    b = await svc.submit_payment(b, sign(svc, b))
    assert b.state == DealState.PAYMENT_FAILED and "authorization_replayed" in events(svc)
    assert svc.ctx.escrow.balance == CAP_UNITS  # type: ignore[attr-defined]


async def test_funded_deal_cannot_be_submitted_again(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    signature = sign(svc, deal)
    deal = await svc.submit_payment(deal, signature)
    with pytest.raises(DealError, match="already submitted"):
        await svc.submit_payment(deal, signature)
    with pytest.raises(GatewayRejected, match="nonce"):  # the gateway refuses the replay on its own as well
        from zyroxq.wallet.signer import mandate_from_signature

        await svc.ctx.gateway.submit_payment_mandate(mandate_from_signature(deal.mandate_draft, signature, deal.cart_contents,
                                                                            deal.payment_request_id, svc.ctx.token))  # fmt: skip


# 8 + 9 ---------------------------------------------------------------------------------------


async def test_budget_limits_deals(svc, influencer):
    states = [(await new_deal(svc, influencer, n)).state for n in range(4)]
    assert states == [DealState.AWAITING_APPROVAL] * 3 + [DealState.BLOCKED]  # $120 budget, $40 caps
    blocked = await svc.get("DEAL-3")
    assert any("budget exceeded" in v for v in blocked.policy_violations)
    assert await reserved(svc) == Decimal("120")


async def test_concurrent_gates_cannot_overspend(svc, influencer):
    carts = [await new_deal(svc, influencer, n, gate=False) for n in range(5)]
    spec = (await svc.campaign("CMP-test")).spec

    async def gate(deal):
        offer = Offer(deal_id=deal.id, campaign_id=spec.id, round=1, fixed_fee=Decimal("20"), cpm=Decimal("2.5"), cap=spec.cap_per_creator,
                      deliverable="youtube_short", publish_within_days=7, required_tags=["#ad"])  # fmt: skip
        return (await svc.gate(deal, spec, offer)).ok

    results = await asyncio.gather(*(gate(d) for d in carts))
    assert sum(results) == 3 and await reserved(svc) == Decimal("120")


async def test_store_budget_reservation_is_atomic():
    from zyroxq.store import MemoryStore

    store = MemoryStore()
    got = await asyncio.gather(*(store.reserve_budget("C", f"D{i}", Decimal("40"), Decimal("120")) for i in range(10)))
    assert sum(got) == 3
    assert await store.reserve_budget("C", "D0", Decimal("40"), Decimal("120"))  # idempotent for the same deal
    await store.release_budget("D0")
    assert await store.reserved_budget("C") == Decimal("80")


async def test_declined_or_failed_deals_release_their_budget(svc, influencer):
    deals = [await new_deal(svc, influencer, n) for n in range(3)]
    await svc.save(deals[0], DealState.REJECTED)  # advertiser declined
    assert await reserved(svc) == Decimal("80")
    assert (await new_deal(svc, influencer, 9)).state == DealState.AWAITING_APPROVAL


# 11 ------------------------------------------------------------------------------------------


class LostResponseGateway(SimulatedGateway):
    """HSP settles the transfer, but our request times out before the response (or webhook) arrives."""

    async def submit_payment_mandate(self, mandate):
        contents = self._carts[mandate.payment_mandate_id]
        self._verify(mandate, contents)
        auth = mandate.authorization
        tx = self.escrow.receive_transfer(auth["from"], int(auth["value"]), auth["nonce"])
        self._payments[mandate.payment_mandate_id] = PaymentLookup(status="payment-finalized", tx_hash=tx, payer=mandate.payer)
        raise httpx.ReadTimeout("timed out")


async def test_timeout_is_unknown_then_reconciled_without_resigning(svc, influencer):
    use_gateway(svc, LostResponseGateway)
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.state == DealState.PAYMENT_UNKNOWN and deal.payment_attempts[0].status == "unknown"
    assert await reserved(svc) == Decimal("40")  # may have settled: the budget stays reserved
    await svc.tick()  # reconciliation asks HSP, then verifies the transaction itself
    deal = await svc.get(deal.id)
    assert deal.state == DealState.FUNDED and len(deal.payment_attempts) == 1 and deal.payment_attempts[0].status == "succeeded"


async def test_slow_gateway_times_out_as_unknown(svc, influencer):
    class Slow(SimulatedGateway):
        async def submit_payment_mandate(self, mandate):
            await asyncio.sleep(5)

    use_gateway(svc, Slow)
    deal = await new_deal(svc, influencer, 1)
    assert (await svc.submit_payment(deal, sign(svc, deal))).state == DealState.PAYMENT_UNKNOWN


async def test_unknown_payment_fails_only_once_the_authorization_provably_expired_unused(svc, influencer):
    class Unreachable(SimulatedGateway):
        async def submit_payment_mandate(self, mandate):
            raise GatewayUnavailable("502 bad gateway")

    use_gateway(svc, Unreachable)
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.state == DealState.PAYMENT_UNKNOWN
    await svc.tick()
    assert (await svc.get(deal.id)).state == DealState.PAYMENT_UNKNOWN  # still valid: it could settle
    svc.ctx.clock.advance(timedelta(hours=2))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.PAYMENT_FAILED and deal.payment_attempts[0].status == "failed"
    assert await reserved(svc) == 0


# 12 ------------------------------------------------------------------------------------------


def finalized_webhook(svc: DealService, deal: Deal, tx: str, status: str = "payment-finalized", request_id: str = "req-1") -> tuple[bytes, str]:
    method = cart_method(deal.cart_contents)
    amount = str(cart_total(deal.cart_contents))
    body = json.dumps({"event_type": "payment", "request_id": request_id, "cart_mandate_id": deal.id, "payment_request_id": deal.payment_request_id,
                       "payer_address": deal.payer, "to_pay_address": method["pay_to"], "order_amount": amount, "pay_amount": amount,
                       "token": method["coin"], "status": status, "tx_signature": tx}).encode()  # fmt: skip
    return body, sign_webhook(body, svc.ctx.gateway.webhook_secret, ts=int(svc.ctx.clock.now().timestamp()))


async def test_duplicate_webhooks_fund_once(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    body, header = finalized_webhook(svc, deal, deal.funding_tx)
    for _ in range(3):
        assert await svc.handle_webhook(body, header)
    assert events(svc).count("escrow_funded") == 1
    assert svc.ctx.escrow.allocated == CAP_UNITS  # type: ignore[attr-defined]
    assert not await svc.handle_webhook(body, "t=1,v1=forged")


# 13 + 14 -------------------------------------------------------------------------------------


class ClaimsSuccessGateway(SimulatedGateway):
    """HSP reports payment-finalized, but no tokens ever reached the escrow."""

    tx = "0x" + "c1" * 32

    async def submit_payment_mandate(self, mandate):
        contents = self._carts[mandate.payment_mandate_id]
        self._verify(mandate, contents)
        await self._emit(contents, mandate, "payment-finalized", self.tx)
        return self.tx


async def test_reported_success_without_funds_is_not_funded(svc, influencer):
    use_gateway(svc, ClaimsSuccessGateway)
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.state != DealState.FUNDED and deal.funding_tx is None
    assert "settlement_pending" in events(svc) and "escrow_funded" not in events(svc)
    assert svc.ctx.escrow.get_deal(deal.id) == {}


async def test_settlement_with_wrong_amount_is_unverified(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    escrow: SimulatedEscrow = svc.ctx.escrow  # type: ignore[assignment]
    tx = escrow.receive_transfer(PAYER, 1_000_000)  # only $1 arrived
    await svc._apply_status(deal.model_copy(update={"state": DealState.SIGNED}), "payment-finalized", tx, PAYER, source="test")
    deal = await svc.get(deal.id)
    assert deal.state != DealState.FUNDED


async def test_one_settlement_cannot_fund_two_deals(svc, influencer):
    a = await new_deal(svc, influencer, 1)
    b = await new_deal(svc, influencer, 2)
    a = await svc.submit_payment(a, sign(svc, a))
    await svc.save(b, DealState.SIGNED)  # as if b had been submitted
    body, header = finalized_webhook(svc, b, a.funding_tx, request_id="req-b")  # HSP (or an attacker) points b at a's transfer
    assert await svc.handle_webhook(body, header)
    b = await svc.get(b.id)
    assert b.state == DealState.PAYMENT_UNKNOWN and b.funding_tx is None and "funding_tx_reused" in events(svc)
    with pytest.raises(EscrowError, match="already used"):  # and the escrow refuses it on its own
        svc.ctx.escrow.register_deal(b.id, PAYER, influencer.wallet, CAP_UNITS, 0)
        svc.ctx.escrow.mark_funded(b.id, a.funding_tx)


async def test_notification_for_wrong_amount_or_recipient_is_not_trusted(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    await svc.save(deal, DealState.SIGNED)
    body = json.loads(finalized_webhook(svc, deal, "0x" + "aa" * 32)[0])
    body["to_pay_address"] = Account.create().address
    await svc.on_payment(body)
    assert (await svc.get(deal.id)).state == DealState.PAYMENT_UNKNOWN and "notification_mismatch" in events(svc)


def test_transfer_log_matching():
    token, payer, escrow = "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20
    from zyroxq.chain.settlement import TRANSFER_TOPIC

    def log(addr, frm, to, value):
        pad = lambda a: bytes(12) + bytes.fromhex(a[2:])  # noqa: E731
        return {"address": addr, "topics": [TRANSFER_TOPIC, pad(frm), pad(to)], "data": "0x" + value.to_bytes(32, "big").hex()}

    assert find_transfer([log(token, payer, escrow, CAP_UNITS)], token, payer, escrow, CAP_UNITS)
    assert not find_transfer([log(token, payer, escrow, CAP_UNITS - 1)], token, payer, escrow, CAP_UNITS)  # amount
    assert not find_transfer([log("0x" + "99" * 20, payer, escrow, CAP_UNITS)], token, payer, escrow, CAP_UNITS)  # token
    assert not find_transfer([log(token, payer, "0x" + "44" * 20, CAP_UNITS)], token, payer, escrow, CAP_UNITS)  # recipient
    assert not find_transfer([log(token, "0x" + "55" * 20, escrow, CAP_UNITS)], token, payer, escrow, CAP_UNITS)  # payer


# 15 ------------------------------------------------------------------------------------------


def test_escrow_funding_and_payout_cannot_double():
    escrow = SimulatedEscrow(verifier=Account.from_key(VERIFIER_KEY).address)
    tx = escrow.receive_transfer(PAYER, CAP_UNITS)
    escrow.register_deal("D1", PAYER, "0x" + "aa" * 20, CAP_UNITS, 0)
    with pytest.raises(EscrowError, match="registered"):
        escrow.register_deal("D1", PAYER, "0x" + "aa" * 20, CAP_UNITS, 0)
    escrow.mark_funded("D1", tx)
    with pytest.raises(EscrowError):
        escrow.mark_funded("D1", tx)
    escrow.register_deal("D2", PAYER, "0x" + "aa" * 20, CAP_UNITS, 0)
    with pytest.raises(EscrowError, match="insufficient"):  # D1's money cannot be counted again for D2
        escrow.mark_funded("D2", "0x" + "bb" * 32)
    with pytest.raises(EscrowError):  # unfunded deals pay nothing
        escrow.release("D2", 1, 1, "0x" + "00" * 65)


def test_deal_state_machine_blocks_skipping_funding():
    assert not transition_allowed(DealState.AWAITING_APPROVAL, DealState.FUNDED)
    assert not transition_allowed(DealState.SIGNED, DealState.VERIFIED)
    assert not transition_allowed(DealState.PAYMENT_UNKNOWN, DealState.PAYMENT_EXPIRED)
    assert not transition_allowed(DealState.COMPLETED, DealState.FUNDED)
    assert transition_allowed(DealState.PAYMENT_UNKNOWN, DealState.FUNDED)


async def test_illegal_transition_is_refused(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    with pytest.raises(DealError, match="not allowed"):
        await svc.save(deal, DealState.FUNDED)


# 16 + 17 -------------------------------------------------------------------------------------


async def test_simulated_settlement_is_labelled_and_cannot_pass_as_real(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    assert deal.funding_simulated and deal.payment_attempts[0].simulated and deal.funding_tx.startswith(SIMULATED_TX_PREFIX)
    funded = next(e for e in svc.ctx.store._events if e.kind == "escrow_funded")  # type: ignore[attr-defined]
    assert funded.detail["simulated"] is True
    # A real-looking transaction hash is no evidence to the simulated verifier
    from zyroxq.chain.settlement import SettlementPending

    with pytest.raises(SettlementPending):
        svc.ctx.settlement.verify_funding("0x" + "de" * 32, PAYER, svc.ctx.escrow.address, CAP_UNITS)
    with pytest.raises(SettlementMismatch):
        svc.ctx.settlement.verify_funding(deal.funding_tx, Account.create().address, svc.ctx.escrow.address, CAP_UNITS)


async def test_hosted_mode_without_credentials_fails_closed():
    with pytest.raises(RuntimeError) as e:
        await build_context(settings(settlement_mode=SettlementMode.HOSTED, hsp_merchant_private_key_path="./does-not-exist.pem"))
    for name in ("HSP_APP_KEY", "HSP_APP_SECRET", "ESCROW_ADDRESS", "OPERATOR_PRIVATE_KEY", "merchant key file"):
        assert name in str(e.value)
    with pytest.raises(RuntimeError, match="simulated cannot fund"):
        await build_context(settings(escrow_address="0x" + "ee" * 20))


# 18 ------------------------------------------------------------------------------------------


async def test_verified_proof_pays_out_once_and_refunds_the_rest(svc, influencer):
    videos = FixtureVideoSource(svc.ctx.clock.now)
    svc.ctx.videos = videos
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    videos.add(VideoInfo(video_id="abcdefghijk", channel_id=influencer.channel_id, title="t", description="@fitfuel #ad",
                         published_at=svc.ctx.clock.now(), privacy_status="public", views=0, duration_seconds=30), week_one_views=4_000)  # fmt: skip
    deal = await svc.submit_proof(deal.id, "https://youtube.com/shorts/abcdefghijk")
    assert deal.state == DealState.VERIFIED, deal.proof
    for _ in range(3):  # extra ticks must not pay twice
        svc.ctx.clock.advance(timedelta(days=4))
        await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.COMPLETED
    kinds = {p.kind: p.amount for p in deal.payouts}
    assert kinds == {"fixed": Decimal("20"), "performance": Decimal("10.00"), "refund": Decimal("10")}
    assert all(p.simulated for p in deal.payouts)
    assert svc.ctx.escrow.balance == 0 and svc.ctx.escrow.allocated == 0  # type: ignore[attr-defined]


async def test_unproven_deal_is_refunded_after_deadline(svc, influencer):
    deal = await new_deal(svc, influencer, 1)
    deal = await svc.submit_payment(deal, sign(svc, deal))
    svc.ctx.clock.advance(timedelta(days=8))
    await svc.tick()
    deal = await svc.get(deal.id)
    assert deal.state == DealState.EXPIRED_REFUNDED and deal.payouts[0].kind == "refund" and deal.payouts[0].amount == Decimal("40")
