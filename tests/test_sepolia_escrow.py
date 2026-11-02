"""PAYMENT_MODE=sepolia on a local EVM (eth-tester): the real CampaignEscrow bytecode, an EIP-3009 test token shaped
like Circle's USDC, and the app's own payment path (ChainGateway → transferWithAuthorization → receipt check →
markFunded → verifier-attested releases). Every balance asserted here is read from the chain."""

import dataclasses
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import solcx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from web3 import EthereumTesterProvider, Web3

from zyroxq.chain import EscrowError, OnchainEscrow, SettlementMismatch, SettlementReverted, attestation_digest, verify_token
from zyroxq.chain.settlement import OnchainSettlementVerifier
from zyroxq.config import SettlementMode
from zyroxq.hsp.networks import find_token
from zyroxq.models import DealState
from zyroxq.services.context import build_sepolia, chain_info
from zyroxq.verifier.attest import Attestor
from zyroxq.wallet.gateway import ChainGateway

from .test_e2e import ATTESTED, client_for, named, onboard_advertiser, onboard_influencer, post_video, server, sign_requests, wallet  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ESCROW = json.loads((ROOT / "contracts" / "build" / "CampaignEscrow.json").read_text(encoding="utf-8"))
OPERATOR = Account.from_key(keccak(text="zyroxq-test-operator"))
VERIFIER = Account.from_key(keccak(text="zyroxq-test-verifier"))
USDC = 10**6


@pytest.fixture(scope="module")
def test_usdc_artifact() -> dict:
    source = (ROOT / "contracts" / "test" / "TestUSDC.sol").read_text(encoding="utf-8")
    out = solcx.compile_standard(
        {"language": "Solidity", "sources": {"TestUSDC.sol": {"content": source}},
         "settings": {"outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}}}},
        solc_version="0.8.24",
    )["contracts"]["TestUSDC.sol"]["TestUSDC"]  # fmt: skip
    return {"abi": out["abi"], "bytecode": out["evm"]["bytecode"]["object"]}


class Chain:
    def __init__(self, artifact: dict, domain_name: str = "USDC") -> None:
        self.w3 = Web3(EthereumTesterProvider())
        self.tester = self.w3.provider.ethereum_tester
        funder = self.w3.eth.accounts[0]
        self.w3.eth.wait_for_transaction_receipt(self.w3.eth.send_transaction({"from": funder, "to": OPERATOR.address, "value": 10**20}))
        self.token_contract = self.deploy(artifact, domain_name)
        self.token = dataclasses.replace(find_token("sepolia", "USDC"), chain_id=self.w3.eth.chain_id, contract_address=self.token_contract.address)
        self.escrow_contract = self.deploy(ESCROW, OPERATOR.address, VERIFIER.address, OPERATOR.address, self.token_contract.address)

    def deploy(self, artifact: dict, *args):
        factory = self.w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        receipt = self.w3.eth.wait_for_transaction_receipt(factory.constructor(*args).transact({"from": self.w3.eth.accounts[0]}))
        return self.w3.eth.contract(address=receipt["contractAddress"], abi=artifact["abi"])

    def mint(self, to: str, units: int) -> None:
        self.w3.eth.wait_for_transaction_receipt(self.token_contract.functions.mint(to, units).transact({"from": self.w3.eth.accounts[0]}))

    def balance(self, who: str) -> int:
        return self.token_contract.functions.balanceOf(who).call()

    def time_travel(self, seconds: int) -> None:
        self.tester.time_travel(self.w3.eth.get_block("latest")["timestamp"] + seconds)


@pytest.fixture
async def sepolia(server, test_usdc_artifact):
    """The app from test_e2e, switched to PAYMENT_MODE=sepolia backends on the local chain."""
    app, videos = server
    chain = Chain(test_usdc_artifact)
    ctx = app.state.zx.ctx
    settings = ctx.settings.model_copy(update={"settlement_mode": SettlementMode.SEPOLIA, "escrow_address": chain.escrow_contract.address,
                                               "sepolia_rpc_url": "https://sepolia.example/v3/secret-provider-key",
                                               "operator_private_key": OPERATOR.key.hex(), "verifier_private_key": VERIFIER.key.hex(),
                                               "settlement_min_confirmations": 1})  # fmt: skip
    ctx.token, ctx.escrow, ctx.gateway, ctx.settlement = build_sepolia(settings, VERIFIER.key.hex(), ctx.clock, w3=chain.w3, token=chain.token)
    ctx.attestor = Attestor(VERIFIER.key.hex(), ctx.escrow.address, ctx.escrow.chain_id, ctx.token.decimals)
    ctx.chain = chain_info(settings, ctx.token.chain_id)
    return app, videos, chain


async def funded_deal(app, videos, chain, mint_units: int = 100 * USDC) -> tuple[dict, httpx.AsyncClient]:
    await onboard_influencer(app, "ava", "fitness", 42_000, "15", "2", "22")
    advertiser = await onboard_advertiser(app)
    if mint_units:
        chain.mint(wallet("advertiser").address, mint_units)
    adv = client_for(app, advertiser)
    cid = (await adv.post("/api/campaigns", json=named(max_creators=1, budget="40"))).json()["spec"]["id"]
    await app.state.zx.runner.wait_idle()
    campaign = (await adv.get(f"/api/campaigns/{cid}")).json()
    res = await adv.post(f"/api/campaigns/{cid}/decision", json={"approved": True, "signatures": sign_requests(campaign, wallet("advertiser"))})
    assert res.status_code == 200, res.text
    await app.state.zx.runner.wait_idle()
    deal = (await adv.get(f"/api/campaigns/{cid}/deals")).json()[0]
    return deal, adv


async def advance(app, hours: float) -> None:
    """The backend's clock moves, then the scheduler runs. (/api/dev/advance refuses on-chain escrows; payout timing is
    the backend's policy, and the contract's own time checks are not involved in releases.)"""
    app.state.zx.ctx.clock.advance(timedelta(hours=hours))
    await app.state.zx.deals.tick()


async def verified(app, videos, deal) -> dict:
    """Submits a passing proof. Verified means the fixed fee is released in the same step."""
    url = post_video(app, videos, "vidAvaSep1", "UCavachannel", "My honest FitFuel review @fitfuel #ad", 9_400)
    res = await client_for(app, "USR-ava").post(f"/api/deals/{deal['id']}/proof", json={"video_url": url, "attestations": ATTESTED})
    assert res.json()["state"] == "FIXED_RELEASED", res.json()
    return res.json()


# ---- deposit -------------------------------------------------------------------------------------------------------


async def test_deposit_moves_real_tokens_and_funds_only_after_the_receipt(sepolia):
    app, videos, chain = sepolia
    deal, adv = await funded_deal(app, videos, chain)
    cap = int(Decimal(deal["terms"]["cap"]) * USDC)
    assert deal["state"] == "FUNDED", deal
    assert chain.balance(chain.escrow_contract.address) == cap  # the advertiser's tokens are in the escrow
    assert chain.balance(wallet("advertiser").address) == 100 * USDC - cap
    receipt = chain.w3.eth.get_transaction_receipt(deal["funding_tx"])
    assert receipt["status"] == 1 and deal["funding_block"] == receipt["blockNumber"] and not deal["funding_simulated"]
    on_chain = chain.escrow_contract.functions.deals(keccak(text=deal["id"])).call()
    assert on_chain[5] == 2 and on_chain[2] == cap  # State.Funded, cap locked
    status = (await adv.get("/api/status")).json()
    assert status["settlement_mode"] == "sepolia" and not status["settlement_simulated"] and status["hsp_in_payment_path"] is False
    assert status["chain"]["explorer_url"] == "https://sepolia.etherscan.io" and status["chain"]["name"] == "Ethereum Sepolia"
    assert "secret-provider-key" not in json.dumps(status)  # the server's RPC (with its API key) never reaches the browser


async def test_empty_wallet_is_refused_before_anything_is_sent(sepolia):
    app, videos, chain = sepolia
    deal, _ = await funded_deal(app, videos, chain, mint_units=0)
    assert deal["state"] == "PAYMENT_FAILED" and deal["funding_tx"] is None
    assert "refused the authorization" in deal["payment_attempts"][0]["error"]
    assert chain.balance(chain.escrow_contract.address) == 0


async def test_restart_reconciles_the_submitted_transaction(sepolia):
    app, videos, chain = sepolia
    ctx = app.state.zx.ctx
    ctx.settlement.min_confirmations = 3  # not final yet when the server first checks
    deal, adv = await funded_deal(app, videos, chain)
    assert deal["state"] == "FUNDING" and deal["payment_attempts"][-1]["tx_hash"]
    # the server restarts: a fresh gateway with no memory of the submission; only the stored hash remains
    ctx.gateway = ChainGateway(ctx.token, ctx.gateway.sender, ctx.clock)
    chain.tester.mine_blocks(2)
    await app.state.zx.deals.tick()
    deal = (await adv.get(f"/api/deals/{deal['id']}")).json()
    assert deal["state"] == "FUNDED" and deal["payment_attempts"][-1]["status"] == "succeeded"


# ---- payouts -------------------------------------------------------------------------------------------------------


async def test_fixed_fee_then_performance_fee_with_real_transfers(sepolia):
    app, videos, chain = sepolia
    deal, adv = await funded_deal(app, videos, chain)
    payee = deal["terms"]["payee"]
    fixed, cap, cpm = (Decimal(deal["terms"][k]) for k in ("fixed_fee", "cap", "cpm"))
    assert chain.balance(payee) == 0
    await verified(app, videos, deal)  # verified → the fixed fee is released at once, no 48 h hold
    after = (await adv.get(f"/api/deals/{deal['id']}")).json()
    assert after["state"] == "FIXED_RELEASED"
    assert chain.balance(payee) == int(fixed * USDC)  # only the fixed fee: the performance fee waits for day 7
    payout = after["payouts"][0]
    receipt = chain.w3.eth.get_transaction_receipt(payout["tx_hash"])
    assert receipt["status"] == 1 and payout["block_number"] == receipt["blockNumber"]
    assert (payout["kind"], payout["to"], payout["chain_id"], payout["token"], payout["simulated"]) == (
        "fixed",
        payee,
        chain.w3.eth.chain_id,
        chain.token_contract.address,
        False,
    )
    released = chain.escrow_contract.events.Released.get_logs(from_block=0)
    assert [(e["args"]["payee"], e["args"]["amount"]) for e in released] == [(payee, int(fixed * USDC))]
    # the creator's deal page builds its Etherscan link as `${chain.explorer_url}/tx/${tx_hash}` (components/Payments.tsx)
    status = (await adv.get("/api/status")).json()
    mine = next(d for d in (await client_for(app, "USR-ava").get("/api/influencer/deals")).json() if d["id"] == deal["id"])
    shown = mine["payouts"][0]
    assert status["escrow"]["simulated"] is False and status["chain"]["explorer_url"] == "https://sepolia.etherscan.io"
    assert (shown["tx_hash"], shown["block_number"], shown["simulated"]) == (payout["tx_hash"], receipt["blockNumber"], False)
    assert re.fullmatch(r"0x[0-9a-f]{64}", shown["tx_hash"])  # the page only links well-formed hashes
    assert mine["funding_tx"] and mine["funding_block"] is not None and mine["funding_simulated"] is False

    await advance(app, 24 * 6 + 23)  # 167 h after publishing: the 7-day measurement is not over
    assert (await adv.get(f"/api/deals/{deal['id']}")).json()["state"] == "FIXED_RELEASED"
    await advance(app, 2)
    done = (await adv.get(f"/api/deals/{deal['id']}")).json()
    assert done["state"] == "COMPLETED"
    performance = min(cap - fixed, Decimal(9_400) / 1000 * cpm).quantize(Decimal("0.01"))
    kinds = {p["kind"]: p for p in done["payouts"]}
    assert Decimal(kinds["performance"]["amount"]) == performance and kinds["performance"]["views"] == 9_400
    assert chain.balance(payee) == int((fixed + performance) * USDC) <= int(cap * USDC)  # never more than the cap
    assert chain.balance(chain.escrow_contract.address) == 0  # the remainder went back to the advertiser
    assert Decimal(kinds["refund"]["amount"]) == cap - fixed - performance and kinds["refund"]["to"] == wallet("advertiser").address


async def test_a_crash_after_sending_records_the_payout_without_paying_twice(sepolia):
    app, videos, chain = sepolia
    deal, adv = await funded_deal(app, videos, chain)
    payee, fixed = deal["terms"]["payee"], Decimal(deal["terms"]["fixed_fee"])
    svc = app.state.zx.deals
    real_record = svc._record_payout

    async def crash(*_a, **_k):
        raise RuntimeError("server died after the release transaction, before saving it")

    svc._record_payout = crash
    url = post_video(app, videos, "vidAvaSep1", "UCavachannel", "My honest FitFuel review @fitfuel #ad", 9_400)
    with pytest.raises(RuntimeError):  # the release runs as part of verifying the proof
        await client_for(app, "USR-ava").post(f"/api/deals/{deal['id']}/proof", json={"video_url": url, "attestations": ATTESTED})
    assert chain.balance(payee) == int(fixed * USDC)  # paid on chain …
    stuck = await svc.get(deal["id"])
    assert stuck.state == DealState.VERIFIED and stuck.payouts == [] and stuck.payout_intent is not None  # … but not recorded

    svc._record_payout = real_record  # restarted
    await svc.tick()
    after = await svc.get(deal["id"])
    assert after.state == DealState.FIXED_RELEASED and [p.kind for p in after.payouts] == ["fixed"]
    assert chain.balance(payee) == int(fixed * USDC)  # still paid exactly once
    assert len(chain.escrow_contract.events.Released.get_logs(from_block=0)) == 1


# ---- the contract's own limits (what the backend can never get around) --------------------------------------------


def attest(escrow: OnchainEscrow, deal_id: str, payee: str, units: int, nonce: int, key=VERIFIER) -> str:
    digest = attestation_digest(escrow.address, escrow.chain_id, deal_id, payee, units, nonce)
    return "0x" + Account.sign_message(encode_defunct(primitive=digest), private_key=key.key).signature.hex().removeprefix("0x")


async def test_contract_refuses_unfunded_unattested_replayed_and_over_cap_releases(sepolia):
    app, _, chain = sepolia
    escrow: OnchainEscrow = app.state.zx.ctx.escrow
    payee = wallet("ava").address
    escrow.register_deal("DEAL-x", wallet("advertiser").address, payee, 4 * USDC, 2**40)
    with pytest.raises(EscrowError):  # registered but not funded
        escrow.release("DEAL-x", 1 * USDC, 1, attest(escrow, "DEAL-x", payee, 1 * USDC, 1))
    with pytest.raises(EscrowError):  # no tokens behind it: cannot be marked funded
        escrow.mark_funded("DEAL-x", "0x" + "ab" * 32)
    chain.mint(escrow.address, 4 * USDC)
    escrow.mark_funded("DEAL-x", "0x" + "ab" * 32)
    with pytest.raises(EscrowError):  # signed by someone other than the verifier
        escrow.release("DEAL-x", 1 * USDC, 1, attest(escrow, "DEAL-x", payee, 1 * USDC, 1, key=OPERATOR))
    with pytest.raises(EscrowError):  # more than the cap
        escrow.release("DEAL-x", 5 * USDC, 2, attest(escrow, "DEAL-x", payee, 5 * USDC, 2))
    good = attest(escrow, "DEAL-x", payee, int(Decimal("1.90") * USDC), 3)
    escrow.release("DEAL-x", int(Decimal("1.90") * USDC), 3, good)
    with pytest.raises(EscrowError):  # the same attestation twice
        escrow.release("DEAL-x", int(Decimal("1.90") * USDC), 3, good)
    assert chain.balance(payee) == int(Decimal("1.90") * USDC)
    assert escrow.find_release("DEAL-x", 3, 0).amount_units == int(Decimal("1.90") * USDC)
    assert escrow.find_release("DEAL-x", 4, 0) is None


def test_look_alike_token_or_wrong_domain_is_refused(test_usdc_artifact):
    chain = Chain(test_usdc_artifact, domain_name="USD Coin")  # same symbol and decimals, different EIP-712 domain
    with pytest.raises(SettlementMismatch, match="EIP-712 domain"):
        verify_token(chain.w3, chain.token)
    with pytest.raises(SettlementMismatch, match="no contract"):
        verify_token(chain.w3, dataclasses.replace(chain.token, contract_address=OPERATOR.address))


def test_escrow_deployed_for_another_token_is_refused(test_usdc_artifact):
    chain = Chain(test_usdc_artifact)
    other = chain.deploy(test_usdc_artifact, "USDC")
    escrow = OnchainEscrow("", chain.escrow_contract.address, OPERATOR.key.hex(), other.address, w3=chain.w3)
    with pytest.raises(EscrowError, match="token"):
        escrow.verify_deployment(chain.w3.eth.chain_id, VERIFIER.address)


def test_a_reverted_transaction_is_never_funding_evidence(test_usdc_artifact):
    chain = Chain(test_usdc_artifact)
    # sent with a fixed gas limit, so it is mined even though it fails (transfer from an empty account)
    tx = chain.token_contract.functions.transfer(chain.escrow_contract.address, USDC).transact({"from": chain.w3.eth.accounts[1], "gas": 100_000})
    assert chain.w3.eth.wait_for_transaction_receipt(tx)["status"] == 0
    verifier = OnchainSettlementVerifier("", chain.token.contract_address, chain.token.chain_id, w3=chain.w3)
    with pytest.raises(SettlementReverted):
        verifier.verify_funding("0x" + bytes(tx).hex(), chain.w3.eth.accounts[1], chain.escrow_contract.address, USDC)


async def test_nothing_is_released_without_a_verified_proof(sepolia):
    app, videos, chain = sepolia
    deal, adv = await funded_deal(app, videos, chain)
    await advance(app, 49)  # time passing alone pays nothing: no proof was ever verified
    after = (await adv.get(f"/api/deals/{deal['id']}")).json()
    assert after["state"] == "FUNDED" and after["payouts"] == [] and after["attestations"] == [] and after["payout_intent"] is None
    assert chain.balance(deal["terms"]["payee"]) == 0 and chain.escrow_contract.events.Released.get_logs(from_block=0) == []
