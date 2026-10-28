from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from zyroxq.chain import EscrowError, SimulatedEscrow
from zyroxq.clock import Clock
from zyroxq.hsp import build_cart_contents, find_token
from zyroxq.models import ProofCheck
from zyroxq.policy import check_cart, check_offer, check_terms, needs_human_approval, performance_payout
from zyroxq.verifier import Attestor
from zyroxq.wallet import GatewayRejected, SimulatedGateway, sign_payment_mandate

from .conftest import ADVERTISER_KEY, VERIFIER_KEY

TOKEN = find_token("hashkey-testnet", "USDC")


def cart_for(terms, pay_to, total=None, expires=None):
    return build_cart_contents(
        terms.deal_id,
        f"PAY-{terms.deal_id}",
        "ZyroXQ",
        TOKEN,
        pay_to,
        [("Fixed fee", terms.fixed_fee), ("Performance (max)", (total or terms.cap) - terms.fixed_fee)],
        expires or datetime.now(UTC) + timedelta(hours=2),
    )


# --- policy ---------------------------------------------------------------


def test_offer_within_bounds(campaign, offer):
    assert check_offer(campaign, offer).ok


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"fixed_fee": Decimal("99")}, "fixed fee"),
        ({"cpm": Decimal("10")}, "CPM"),
        ({"cap": Decimal("400")}, "cap"),
        ({"round": 5}, "rounds"),
        ({"required_tags": []}, "tags"),
    ],
)
def test_offer_violations(campaign, offer, change, needle):
    r = check_offer(campaign, offer.model_copy(update=change))
    assert not r.ok and any(needle in v for v in r.violations)


def test_terms_signed_by_payee(campaign, influencer, offer, terms):
    assert check_terms(campaign, terms, influencer, offer).ok


def test_terms_payee_swap_is_blocked(campaign, influencer, offer, terms):
    attacker = Account.create().address
    r = check_terms(campaign, terms.model_copy(update={"payee": attacker}), influencer, offer)
    assert not r.ok
    assert any("payee" in v for v in r.violations) and any("signature" in v for v in r.violations)


def test_terms_amount_tamper_breaks_signature(campaign, influencer, offer, terms):
    r = check_terms(campaign, terms.model_copy(update={"cap": Decimal("400")}), influencer, offer)
    assert not r.ok


def test_terms_signed_by_authorized_agent_key(campaign, identity, offer, terms):
    from zyroxq.wallet.signer import sign_terms

    by_agent = sign_terms(identity.private_key, terms)
    assert check_terms(campaign, by_agent, identity.profile, offer).ok
    # The wallet itself is no longer the expected signer once an agent key is registered
    assert not check_terms(campaign, terms, identity.profile, offer).ok


def test_agent_key_without_wallet_delegation_is_blocked(campaign, identity, offer, terms):
    from zyroxq.wallet.signer import sign_terms

    forged = Account.sign_message(encode_defunct(text=identity.profile.delegation), "0x" + "99" * 32).signature.hex()
    profile = identity.profile.model_copy(update={"delegation_signature": "0x" + forged.removeprefix("0x")})
    result = check_terms(campaign, sign_terms(identity.private_key, terms), profile, offer)
    assert "agent key is not authorized by the influencer's wallet" in result.violations


def test_cart_checks(campaign, terms):
    escrow = "0x" + "ee" * 20
    now = datetime.now(UTC)
    assert check_cart(campaign, terms, cart_for(terms, escrow), TOKEN, escrow, Decimal("0"), now).ok
    assert not check_cart(campaign, terms, cart_for(terms, "0x" + "bad0" * 10), TOKEN, escrow, Decimal("0"), now).ok
    assert not check_cart(campaign, terms, cart_for(terms, escrow, total=Decimal("41")), TOKEN, escrow, Decimal("0"), now).ok
    assert not check_cart(campaign, terms, cart_for(terms, escrow), TOKEN, escrow, Decimal("100"), now).ok  # budget
    assert not check_cart(campaign, terms, cart_for(terms, escrow, expires=now - timedelta(minutes=1)), TOKEN, escrow, Decimal("0"), now).ok


def test_approval_and_performance(terms):
    assert needs_human_approval(Decimal("50.01"), Decimal("50"))
    assert not needs_human_approval(Decimal("50"), Decimal("50"))
    assert performance_payout(terms, 4_000) == Decimal("10.00")  # 4 × 2.5
    assert performance_payout(terms, 1_000_000) == terms.cap - terms.fixed_fee  # capped
    assert performance_payout(terms, 333) == Decimal("0.83")  # rounded down


# --- gateway + mandate -----------------------------------------------------


async def funded_gateway(terms):
    clock = Clock()
    escrow = SimulatedEscrow(verifier=Account.from_key(VERIFIER_KEY).address)
    gw = SimulatedGateway(TOKEN, escrow, clock, "http://test")
    events: list[bytes] = []

    async def sink(raw, header):
        events.append(raw)

    gw.sink = sink
    contents = cart_for(terms, escrow.address)
    order = await gw.create_order(contents)
    return clock, escrow, gw, contents, order, events


async def test_payment_mandate_verified_and_settled(terms):
    clock, escrow, gw, contents, order, events = await funded_gateway(terms)
    now = int(clock.now().timestamp())
    mandate = sign_payment_mandate(ADVERTISER_KEY, contents, order.payment_request_id, TOKEN, now - 60, now + 3600)
    assert mandate.authorization["value"] == "40000000"
    await gw.submit_payment_mandate(mandate)
    assert escrow.balance == 40_000_000
    assert [b'"payment-included"' in e for e in events] == [True, False]
    with pytest.raises(GatewayRejected, match="nonce"):
        await gw.submit_payment_mandate(mandate)  # replay


async def test_gateway_rejects_tampered_mandate(terms):
    clock, escrow, gw, contents, order, _ = await funded_gateway(terms)
    now = int(clock.now().timestamp())
    mandate = sign_payment_mandate(ADVERTISER_KEY, contents, order.payment_request_id, TOKEN, now - 60, now + 3600)
    mandate.x402["payload"]["authorization"]["to"] = Account.create().address
    with pytest.raises(GatewayRejected, match="payee"):
        await gw.submit_payment_mandate(mandate)
    mandate = sign_payment_mandate(ADVERTISER_KEY, contents, order.payment_request_id, TOKEN, now - 60, now + 3600)
    mandate.x402["payload"]["authorization"]["value"] = "1"
    with pytest.raises(GatewayRejected, match="amount"):
        await gw.submit_payment_mandate(mandate)


# --- escrow + attestation --------------------------------------------------


def test_escrow_release_requires_verifier_attestation(terms):
    escrow = SimulatedEscrow(verifier=Account.from_key(VERIFIER_KEY).address)
    escrow.credit(40_000_000)
    escrow.register_deal(terms.deal_id, Account.from_key(ADVERTISER_KEY).address, terms.payee, 40_000_000, 0)
    escrow.mark_funded(terms.deal_id)
    passed = [ProofCheck(name="exists", passed=True)]

    forged = Attestor("0x" + "44" * 32, escrow.address, escrow.chain_id).attest(terms.deal_id, "v", terms.payee, Decimal("20"), "fixed", passed)
    with pytest.raises(EscrowError, match="attestation"):
        escrow.release(terms.deal_id, 20_000_000, forged.nonce, forged.signature)

    attestor = Attestor(VERIFIER_KEY, escrow.address, escrow.chain_id)
    good = attestor.attest(terms.deal_id, "v", terms.payee, Decimal("20"), "fixed", passed)
    escrow.release(terms.deal_id, 20_000_000, good.nonce, good.signature)
    with pytest.raises(EscrowError):
        escrow.release(terms.deal_id, 20_000_000, good.nonce, good.signature)  # replay

    too_much = attestor.attest(terms.deal_id, "v", terms.payee, Decimal("30"), "performance", passed)
    with pytest.raises(EscrowError, match="cap"):
        escrow.release(terms.deal_id, 30_000_000, too_much.nonce, too_much.signature)

    _, refunded = escrow.close(terms.deal_id)
    assert refunded == 20_000_000 and escrow.balance == 0


def test_attestor_refuses_failed_checks(terms):
    attestor = Attestor(VERIFIER_KEY, "0x" + "ee" * 20, 133)
    with pytest.raises(ValueError):
        attestor.attest(terms.deal_id, "v", terms.payee, Decimal("1"), "fixed", [ProofCheck(name="x", passed=False)])


def test_escrow_needs_funding_before_mark_funded(terms):
    escrow = SimulatedEscrow(verifier=Account.from_key(VERIFIER_KEY).address)
    escrow.register_deal(terms.deal_id, "0x" + "aa" * 20, terms.payee, 40_000_000, 0)
    with pytest.raises(EscrowError, match="funding"):
        escrow.mark_funded(terms.deal_id)
