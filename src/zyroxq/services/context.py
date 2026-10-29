"""Wires backends from settings. Simulated pieces are used only when the real ones aren't configured,
and hosted (real HSP) mode refuses to start without an on-chain escrow so funds never go to a fake address.

Influencers come from the user store (see registry.py); payers sign in their own browser wallet, so
the server holds no advertiser key."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from eth_account import Account
from langchain_core.language_models import BaseChatModel

from ..agents.a2a_client import A2AConnector
from ..chain.balances import BalanceReader, RpcBalanceReader
from ..chain.escrow import Escrow, OnchainEscrow, SimulatedEscrow
from ..chain.settlement import OnchainSettlementVerifier, SettlementVerifier, SimulatedSettlementVerifier
from ..clock import Clock
from ..compliance import DisclosureRegistry
from ..config import Settings, SettlementMode
from ..hsp.client import HspClient
from ..hsp.networks import TokenInfo, find_token
from ..store import MemoryStore, PostgresStore, Store
from ..verifier.attest import Attestor
from ..verifier.youtube import VideoSource, YouTubeDataApi
from ..wallet.gateway import Gateway, HostedGateway, SimulatedGateway
from .events import EventBus
from .registry import InfluencerRegistry


@dataclass
class AppContext:
    settings: Settings
    store: Store
    clock: Clock
    token: TokenInfo
    escrow: Escrow
    gateway: Gateway
    settlement: SettlementVerifier  # independent evidence that a payment reached the escrow
    balances: BalanceReader  # advisory wallet balances for the signing panel
    attestor: Attestor
    videos: VideoSource | None  # None until YOUTUBE_API_KEY is set: proofs cannot be verified
    a2a: A2AConnector
    llm: BaseChatModel | None
    registry: InfluencerRegistry
    disclosure: DisclosureRegistry  # versioned sponsorship-disclosure policies
    bus: EventBus = field(default_factory=EventBus)
    warnings: list[str] = field(default_factory=list)


def chat_model(settings: Settings) -> BaseChatModel | None:
    if not settings.anthropic_api_key:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=settings.llm_model, api_key=settings.anthropic_api_key, max_tokens=1024)


def hosted_mode_missing(settings: Settings) -> list[str]:
    """Configuration hosted (real HSP) mode cannot run without."""
    required = {
        "HSP_APP_KEY": settings.hsp_app_key,
        "HSP_APP_SECRET": settings.hsp_app_secret,
        "ESCROW_ADDRESS": settings.escrow_address,
        "OPERATOR_PRIVATE_KEY": settings.operator_private_key,
        "VERIFIER_PRIVATE_KEY": settings.verifier_private_key,  # must match the deployed escrow's verifier
    }
    missing = [name for name, value in required.items() if not value]
    if not Path(settings.hsp_merchant_private_key_path).is_file():
        missing.append(f"HSP merchant key file ({settings.hsp_merchant_private_key_path})")
    return missing


async def build_context(
    settings: Settings,
    a2a_http: httpx.AsyncClient | None = None,
    clock: Clock | None = None,
    llm: BaseChatModel | None = None,
) -> AppContext:
    warnings: list[str] = []
    clock = clock or Clock()
    token = find_token(settings.hsp_network, settings.hsp_coin)

    store: Store
    if settings.database_url:
        store = await PostgresStore.connect(settings.database_url)
    else:
        store = MemoryStore()
        warnings.append("DATABASE_URL not set: using the in-memory store (data is lost on restart)")

    verifier_key = settings.verifier_private_key
    if not verifier_key:
        verifier_key = "0x" + secrets.token_hex(32)
        warnings.append("VERIFIER_PRIVATE_KEY not set: using a random verifier key for this run")
    secret = settings.session_secret
    if not secret:
        secret = secrets.token_hex(32)
        warnings.append("SESSION_SECRET not set: sessions and influencer agent keys reset on restart")

    escrow: Escrow
    gateway: Gateway
    settlement: SettlementVerifier
    if settings.settlement_mode == SettlementMode.HOSTED:
        # Real money: fail closed on anything missing, and never fall back to simulation
        missing = hosted_mode_missing(settings)
        if missing:
            raise RuntimeError("SETTLEMENT_MODE=hosted is unavailable, missing: " + ", ".join(missing))
        onchain = OnchainEscrow(settings.hashkey_testnet_rpc_url, settings.escrow_address, settings.operator_private_key, token.contract_address)
        onchain.verify_deployment(token.chain_id, Account.from_key(verifier_key).address)
        escrow = onchain
        pem = Path(settings.hsp_merchant_private_key_path).read_bytes()  # noqa: ASYNC240 - one-off read at startup
        client = HspClient(settings.hsp_base_url, settings.hsp_app_key, settings.hsp_app_secret, settings.hsp_merchant_name, pem)
        gateway = HostedGateway(client, redirect_url=f"{settings.public_base_url}/")
        settlement = OnchainSettlementVerifier(settings.hashkey_testnet_rpc_url, token.contract_address, token.chain_id,
                                               settings.settlement_min_confirmations)  # fmt: skip
    else:
        if settings.escrow_address:
            raise RuntimeError("SETTLEMENT_MODE=simulated cannot fund an on-chain escrow; unset ESCROW_ADDRESS or use hosted mode")
        sim = SimulatedEscrow(verifier=Account.from_key(verifier_key).address, chain_id=token.chain_id)
        escrow = sim
        gateway = SimulatedGateway(token, sim, clock, settings.public_base_url)
        settlement = SimulatedSettlementVerifier(sim, token.contract_address)
        warnings.append("SETTLEMENT_MODE=simulated: HSP, escrow, payouts and refunds are emulated in-process; no real USDC moves")

    videos: VideoSource | None = None
    if settings.youtube_api_key:
        videos = YouTubeDataApi(settings.youtube_api_key)
    else:
        warnings.append("YOUTUBE_API_KEY not set: video proofs cannot be verified")
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        warnings.append("GOOGLE_OAUTH_CLIENT_ID / _SECRET not set: nobody can sign in")

    ctx = AppContext(
        settings=settings,
        store=store,
        clock=clock,
        token=token,
        escrow=escrow,
        gateway=gateway,
        settlement=settlement,
        balances=RpcBalanceReader(settings.hashkey_testnet_rpc_url, token.contract_address),
        attestor=Attestor(verifier_key, escrow.address, escrow.chain_id, token.decimals),
        videos=videos,
        a2a=A2AConnector(a2a_http or httpx.AsyncClient(timeout=60)),
        llm=llm if llm is not None else chat_model(settings),
        registry=InfluencerRegistry(store, settings.public_base_url, secret),
        disclosure=DisclosureRegistry(store),
        warnings=warnings,
    )
    await ctx.disclosure.load()
    ctx.settings = settings.model_copy(update={"session_secret": secret})
    if ctx.llm is None:
        warnings.append("ANTHROPIC_API_KEY not set: agents negotiate with deterministic strategies")
    return ctx
