"""Wires backends from settings. Simulated pieces are used only when the real ones aren't configured,
and hosted (real HSP) mode refuses to start without an on-chain escrow so funds never go to a fake address."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
from eth_account import Account
from langchain_core.language_models import BaseChatModel

from ..agents.a2a_client import A2AConnector
from ..chain.escrow import Escrow, OnchainEscrow, SimulatedEscrow
from ..clock import Clock
from ..config import Settings, SettlementMode
from ..demo.fixtures import DEMO_INFLUENCERS, DemoInfluencer, demo_key
from ..hsp.client import HspClient
from ..hsp.networks import TokenInfo, find_token
from ..models import InfluencerProfile
from ..store import MemoryStore, PostgresStore, Store
from ..verifier.attest import Attestor
from ..verifier.youtube import FixtureVideoSource, VideoSource, YouTubeDataApi
from ..wallet.gateway import Gateway, HostedGateway, SimulatedGateway


@dataclass
class Registration:
    profile: InfluencerProfile
    agent_url: str


@dataclass
class AppContext:
    settings: Settings
    store: Store
    clock: Clock
    token: TokenInfo
    escrow: Escrow
    gateway: Gateway
    attestor: Attestor
    videos: VideoSource
    fixture_videos: FixtureVideoSource | None
    advertiser_key: str
    a2a: A2AConnector
    llm: BaseChatModel | None
    registry: dict[str, Registration] = field(default_factory=dict)
    demo_influencers: dict[str, DemoInfluencer] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def advertiser_address(self) -> str:
        return Account.from_key(self.advertiser_key).address


def chat_model(settings: Settings) -> BaseChatModel | None:
    if not settings.anthropic_api_key:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=settings.llm_model, api_key=settings.anthropic_api_key, temperature=0.3, max_tokens=1024)


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

    verifier_key = settings.verifier_private_key or demo_key("verifier")
    advertiser_key = settings.advertiser_private_key or demo_key("advertiser")
    if not settings.verifier_private_key or not settings.advertiser_private_key:
        warnings.append("Using demo verifier/advertiser keys derived from public seeds")

    escrow: Escrow
    if settings.escrow_address and settings.operator_private_key:
        escrow = OnchainEscrow(settings.hashkey_testnet_rpc_url, settings.escrow_address, settings.operator_private_key, token.contract_address)
    else:
        escrow = SimulatedEscrow(verifier=Account.from_key(verifier_key).address, chain_id=token.chain_id)

    gateway: Gateway
    if settings.settlement_mode == SettlementMode.HOSTED:
        if escrow.simulated:
            raise RuntimeError("SETTLEMENT_MODE=hosted needs ESCROW_ADDRESS and OPERATOR_PRIVATE_KEY (real funds go to the escrow)")
        pem = Path(settings.hsp_merchant_private_key_path).read_bytes()  # noqa: ASYNC240 - one-off read at startup
        client = HspClient(settings.hsp_base_url, settings.hsp_app_key, settings.hsp_app_secret, settings.hsp_merchant_name, pem)
        gateway = HostedGateway(client, redirect_url=f"{settings.public_base_url}/")
    else:
        if not isinstance(escrow, SimulatedEscrow):
            raise RuntimeError("SETTLEMENT_MODE=simulated cannot fund an on-chain escrow; unset ESCROW_ADDRESS or use hosted mode")
        gateway = SimulatedGateway(token, escrow, clock, settings.public_base_url)
        warnings.append("SETTLEMENT_MODE=simulated: HSP gateway and escrow are emulated in-process")

    fixture_videos: FixtureVideoSource | None = None
    videos: VideoSource
    if settings.youtube_api_key:
        videos = YouTubeDataApi(settings.youtube_api_key)
    else:
        videos = fixture_videos = FixtureVideoSource(clock.now)
        warnings.append("YOUTUBE_API_KEY not set: proof checks use demo videos")

    ctx = AppContext(
        settings=settings,
        store=store,
        clock=clock,
        token=token,
        escrow=escrow,
        gateway=gateway,
        attestor=Attestor(verifier_key, escrow.address, escrow.chain_id, token.decimals),
        videos=videos,
        fixture_videos=fixture_videos,
        advertiser_key=advertiser_key,
        a2a=A2AConnector(a2a_http or httpx.AsyncClient(timeout=60)),
        llm=llm if llm is not None else chat_model(settings),
        warnings=warnings,
    )
    if ctx.llm is None:
        warnings.append("ANTHROPIC_API_KEY not set: agents negotiate with deterministic strategies")

    base = settings.public_base_url.rstrip("/")
    for demo in DEMO_INFLUENCERS:
        ctx.demo_influencers[demo.profile.id] = demo
        ctx.registry[demo.profile.id] = Registration(demo.profile, f"{base}/a2a/influencers/{demo.profile.id}/")
    return ctx
