"""ZyroXQ platform server: Google sign-in, REST API + live event stream for the React app, HSP
webhook, A2A influencer agents (one per onboarded creator) and the MCP endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.sessions import SessionMiddleware
from web3 import Web3

from ..agents.advertiser.assistant import Assistant
from ..agents.advertiser.runner import CampaignRunner
from ..agents.influencer.hub import InfluencerHub
from ..auth.crypto import new_agent_key
from ..auth.google import GoogleOAuth, OAuthError
from ..auth.tokens import issue_user_token, read_user_token
from ..auth.wallet_link import link_message
from ..clock import Clock
from ..config import Settings, get_settings
from ..mcp_servers.server import build_mcp
from ..models import Campaign, CampaignSpec, Deal, DealState, RateCard, User, new_id
from ..services.context import AppContext, build_context, chat_model
from ..services.deals import DealError, DealService
from ..verifier.youtube import parse_video_id
from ..wallet.signer import recover_personal_sign

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"  # the built React app (web/ → npm run build)
OPERATOR = "operator"  # read-only principal for the stdio MCP server


class CampaignCreate(BaseModel):
    advertiser: str = Field(min_length=1, max_length=80)
    brand_handle: str = Field(pattern=r"^@[A-Za-z0-9_.]{2,40}$")
    niche: str = Field(default="fitness", min_length=2, max_length=40)
    budget: Decimal = Field(gt=0, le=10_000)
    max_creators: int = Field(default=3, ge=1, le=10)
    min_subscribers: int = Field(default=0, ge=0)
    max_subscribers: int = Field(default=10_000_000, ge=1)
    max_fixed: Decimal = Field(gt=0)
    max_cpm: Decimal = Field(ge=0)
    cap_per_creator: Decimal = Field(gt=0)
    publish_within_days: int = Field(default=7, ge=1, le=60)
    brief: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def bounds(self) -> CampaignCreate:
        if self.max_fixed > self.cap_per_creator:
            raise ValueError("max_fixed cannot exceed cap_per_creator")
        if self.cap_per_creator > self.budget:
            raise ValueError("cap_per_creator cannot exceed the budget")
        for v in (self.budget, self.max_fixed, self.max_cpm, self.cap_per_creator):
            if v != v.quantize(Decimal("0.01")):
                raise ValueError("amounts must have at most 2 decimals")
        return self


class Decision(BaseModel):
    approved: bool
    signatures: dict[str, str] = Field(default_factory=dict)  # deal_id → EIP-712 signature from the payer's wallet


class ProofIn(BaseModel):
    video_url: str = Field(max_length=300)


class DisputeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdvanceIn(BaseModel):
    hours: float = Field(gt=0, le=24 * 30)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class BriefIn(BaseModel):
    brief: str = Field(min_length=10, max_length=2000)


class AdvertiserProfileIn(BaseModel):
    company: str = Field(min_length=1, max_length=80)
    brand_handle: str = Field(pattern=r"^@[A-Za-z0-9_.]{2,40}$")


class InfluencerProfileIn(BaseModel):
    niche: str = Field(min_length=2, max_length=40)
    fixed_min: Decimal = Field(gt=0, le=100_000)
    cpm_min: Decimal = Field(ge=0, le=1_000)
    preferred_fixed: Decimal = Field(gt=0, le=100_000)
    persona: str = Field(default="", max_length=500)
    agent_active: bool = True

    @model_validator(mode="after")
    def anchor(self) -> InfluencerProfileIn:
        if self.preferred_fixed < self.fixed_min:
            raise ValueError("preferred_fixed must be at least fixed_min")
        return self


class WalletChallengeIn(BaseModel):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")


class WalletLinkIn(BaseModel):
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


class State:
    settings: Settings
    ctx: AppContext
    deals: DealService
    runner: CampaignRunner
    assistant: Assistant | None
    hub: InfluencerHub


# ---- principals ------------------------------------------------------------------


async def principal(request: Request) -> User | str:
    """Session cookie (browser), or a bearer token: a user token from the assistant, or the operator token."""
    state: State = request.app.state.zx
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if state.settings.mcp_api_token and hmac.compare_digest(token, state.settings.mcp_api_token):
            return OPERATOR
        uid = read_user_token(state.settings.session_secret, token)
    else:
        uid = request.session.get("uid")
    user = await state.ctx.store.get_user(uid) if uid else None
    if user is None:
        raise HTTPException(401, "sign in first")
    return user


Principal = Annotated[User | str, Depends(principal)]


async def current_user(who: Principal) -> User:
    if not isinstance(who, User):
        raise HTTPException(403, "this token is read-only")
    return who


CurrentUser = Annotated[User, Depends(current_user)]


async def advertiser(user: CurrentUser) -> User:
    if user.role != "advertiser":
        raise HTTPException(403, "advertisers only")
    return user


async def influencer(user: CurrentUser) -> User:
    if user.role != "influencer":
        raise HTTPException(403, "influencers only")
    return user


AdvertiserUser = Annotated[User, Depends(advertiser)]
InfluencerUser = Annotated[User, Depends(influencer)]


async def _checkpointer(settings: Settings, stack: contextlib.AsyncExitStack) -> BaseCheckpointSaver:
    if not settings.database_url:
        return InMemorySaver()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(settings.database_url, open=False, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await pool.open(wait=True, timeout=10)
    stack.push_async_callback(pool.close)
    saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await saver.setup()
    return saver


def create_app(
    settings: Settings | None = None,
    http_factory: Callable[[FastAPI], httpx.AsyncClient] | None = None,
    scheduler_interval: float | None = 3.0,
) -> FastAPI:
    """`http_factory` builds the client used for outbound A2A and MCP-tool calls (tests pass an in-process ASGI client)."""
    settings = settings or get_settings()
    ephemeral_secret = not settings.session_secret
    if ephemeral_secret:
        settings = settings.model_copy(update={"session_secret": secrets.token_hex(32)})
    clock = Clock()
    llm = chat_model(settings)
    base = settings.public_base_url.rstrip("/")
    frontend = (settings.frontend_url or base).rstrip("/")
    oauth = GoogleOAuth(settings.google_oauth_client_id, settings.google_oauth_client_secret, f"{base}/auth/google/callback")
    state = State()
    state.settings = settings

    app = FastAPI(title="ZyroXQ", version="0.2.0", lifespan=lambda app: lifespan(app))
    app.state.zx = state
    app.add_middleware(
        SessionMiddleware, secret_key=settings.session_secret, session_cookie="zyroxq_session", max_age=7 * 24 * 3600,
        same_site="lax", https_only=base.startswith("https://"),
    )  # fmt: skip
    http = http_factory(app) if http_factory else httpx.AsyncClient(base_url=base, timeout=60)
    mcp = build_mcp(base, http=http, streamable_http_path="/", default_token=settings.mcp_api_token)
    app.state.mcp = mcp
    state.hub = InfluencerHub(lambda i: state.ctx.registry.identity(i), lambda i: state.ctx.registry.agent_url(i), clock, llm)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            state.ctx = await build_context(settings, a2a_http=http, clock=clock, llm=llm)
            if ephemeral_secret:
                state.ctx.warnings.append("SESSION_SECRET not set: sign-ins and influencer agent keys reset on restart")
            state.deals = DealService(state.ctx)
            if hasattr(state.ctx.gateway, "sink"):
                state.ctx.gateway.sink = state.deals.handle_webhook  # simulated HSP delivers webhooks in-process
            state.runner = CampaignRunner(state.deals, await _checkpointer(settings, stack))
            state.assistant = Assistant(llm, f"{base}/mcp/") if llm is not None else None
            await stack.enter_async_context(mcp.session_manager.run())
            for w in state.ctx.warnings:
                log.warning(w)
            ticker = asyncio.create_task(_scheduler(state, scheduler_interval)) if scheduler_interval else None
            try:
                yield
            finally:
                if ticker:
                    ticker.cancel()
                await state.runner.wait_idle()
                if hasattr(state.ctx.store, "close"):
                    await state.ctx.store.close()

    app.mount("/a2a/influencers", state.hub)
    app.mount("/mcp", mcp.streamable_http_app())

    @app.exception_handler(DealError)
    async def deal_error(_: Request, e: DealError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    # ---- access rules -------------------------------------------------------------

    async def readable_campaign(campaign_id: str, who: User | str) -> Campaign:
        campaign = await state.deals.campaign(campaign_id)
        if who != OPERATOR and not (isinstance(who, User) and campaign.spec.owner_id == who.id):
            raise HTTPException(404, "campaign not found")
        return campaign

    async def readable_deal(deal_id: str, who: User | str) -> Deal:
        deal = await state.deals.get(deal_id)
        if who == OPERATOR or (isinstance(who, User) and deal.influencer_id == who.id):
            return deal
        await readable_campaign(deal.campaign_id, who)
        return deal

    # ---- sign-in -------------------------------------------------------------

    def to_frontend(path: str, error: str | None = None) -> RedirectResponse:
        return RedirectResponse(f"{frontend}{path}" + (f"?error={quote(error)}" if error else ""), status_code=303)

    @app.get("/auth/google/login", include_in_schema=False)
    async def google_login(request: Request, role: Literal["advertiser", "influencer"] = "advertiser") -> RedirectResponse:
        if not oauth.configured:
            return to_frontend("/login", "Google sign-in is not configured on the server (GOOGLE_OAUTH_CLIENT_ID / _SECRET)")
        oauth_state = secrets.token_urlsafe(24)
        request.session["oauth"] = {"state": oauth_state, "role": role}
        return RedirectResponse(oauth.authorize_url(oauth_state, youtube=role == "influencer"), status_code=303)

    @app.get("/auth/google/callback", include_in_schema=False)
    async def google_callback(request: Request, code: str = "", error: str = "") -> RedirectResponse:
        pending = request.session.pop("oauth", None)
        oauth_state = request.query_params.get("state", "")
        if error or not code:
            return to_frontend("/login", f"Google sign-in was cancelled ({error or 'no code'})")
        if not pending or not secrets.compare_digest(pending["state"], oauth_state):
            return to_frontend("/login", "sign-in expired, please try again")
        try:
            tokens = await oauth.exchange(code)
            info = await oauth.userinfo(tokens["access_token"])
            user = await state.ctx.store.get_user_by_email(info["email"])
            role = user.role if user else pending["role"]
            channel = await oauth.my_channel(tokens["access_token"], tokens.get("scope")) if role == "influencer" and pending["role"] == "influencer" else None
        except OAuthError as e:
            return to_frontend("/login", str(e))
        if user is None:
            if role == "influencer" and channel is None:
                return to_frontend("/login", "influencers must sign in with the Influencer button to connect YouTube")
            user = User(id=new_id("USR"), email=info["email"], name=info.get("name", info["email"]), picture=info.get("picture", ""),
                        role=role, created_at=state.ctx.clock.now())  # fmt: skip
            if role == "influencer":
                user.agent_signer, user.agent_key_encrypted = new_agent_key(settings.session_secret)
        user.name, user.picture = info.get("name", user.name), info.get("picture", user.picture)
        if channel is not None:
            if user.channel and user.channel.channel_id != channel.channel_id:
                user.delegation = user.delegation_signature = ""  # the wallet must re-authorize the agent for the new channel
            user.channel = channel
        await state.ctx.store.save_user(user)
        request.session.clear()
        request.session["uid"] = user.id
        return to_frontend("/app")

    @app.post("/auth/logout")
    async def logout(request: Request) -> dict[str, str]:
        request.session.clear()
        return {"status": "signed out"}

    # ---- account + onboarding ---------------------------------------------------

    def me_payload(user: User) -> dict[str, Any]:
        out: dict[str, Any] = {"user": user.public()}
        if user.role == "influencer":
            profile = user.influencer_profile()
            out["profile_complete"] = profile is not None
            out["agent_url"] = state.ctx.registry.agent_url(user.id)
            out["agent_listed"] = bool(profile and user.agent_active and user.delegation_signature)
        else:
            out["profile_complete"] = bool(user.wallet and user.company and user.brand_handle)
        return out

    @app.get("/api/me")
    async def me(user: CurrentUser) -> dict[str, Any]:
        return me_payload(user)

    @app.put("/api/me/advertiser")
    async def update_advertiser(body: AdvertiserProfileIn, user: AdvertiserUser) -> dict[str, Any]:
        user.company, user.brand_handle = body.company, body.brand_handle
        await state.ctx.store.save_user(user)
        return me_payload(user)

    @app.put("/api/me/influencer")
    async def update_influencer(body: InfluencerProfileIn, user: InfluencerUser) -> dict[str, Any]:
        user.niche = body.niche.strip().lower()
        user.rate_card = RateCard(fixed_min=body.fixed_min, cpm_min=body.cpm_min, preferred_fixed=body.preferred_fixed)
        user.persona = body.persona.strip()
        user.agent_active = body.agent_active
        await state.ctx.store.save_user(user)
        return me_payload(user)

    @app.post("/api/me/wallet/challenge")
    async def wallet_challenge(request: Request, body: WalletChallengeIn, user: CurrentUser) -> dict[str, str]:
        if user.role == "influencer" and user.channel is None:
            raise HTTPException(409, "connect your YouTube channel first")
        message = link_message(user, body.address, state.ctx.clock.now())
        request.session["wallet_challenge"] = {"address": Web3.to_checksum_address(body.address), "message": message}
        return {"message": message}

    @app.post("/api/me/wallet")
    async def wallet_link(request: Request, body: WalletLinkIn, user: CurrentUser) -> dict[str, Any]:
        challenge = request.session.pop("wallet_challenge", None)
        if not challenge:
            raise HTTPException(409, "request a challenge first")
        signer = recover_personal_sign(challenge["message"], body.signature)
        if signer is None or signer.lower() != challenge["address"].lower():
            raise HTTPException(400, "signature does not match the wallet")
        user.wallet = challenge["address"]
        if user.role == "influencer":
            user.delegation, user.delegation_signature = challenge["message"], body.signature
        await state.ctx.store.save_user(user)
        return me_payload(user)

    # ---- platform ------------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        ctx = state.ctx
        return {
            "settlement_mode": ctx.gateway.name,
            "browser_signing": ctx.gateway.agent_signing,
            "escrow": {"address": ctx.escrow.address, "simulated": ctx.escrow.simulated, "chain_id": ctx.escrow.chain_id},
            "token": {"coin": ctx.token.coin, "network": ctx.token.network, "address": ctx.token.contract_address, "decimals": ctx.token.decimals},
            "chain": {
                "id": ctx.token.chain_id,
                "name": "HashKey Chain Testnet",
                "rpc_url": settings.hashkey_testnet_rpc_url,
                "explorer_url": settings.hashkey_testnet_explorer_url,
                "currency": "HSK",
            },  # fmt: skip
            "verifier": ctx.attestor.address,
            "verifier_online": ctx.videos is not None,
            "login_configured": oauth.configured,
            "llm": settings.llm_model if ctx.llm else None,
            "store": type(ctx.store).__name__,
            "clock_offset_hours": ctx.clock.offset_hours,
            "now": ctx.clock.now().isoformat(),
            "warnings": ctx.warnings,
        }

    @app.get("/api/influencers")
    async def influencers(_: CurrentUser) -> list[dict[str, Any]]:
        """Directory of live influencer agents. Rate-card floors stay private to each agent."""
        return [
            {
                **reg.profile.model_dump(mode="json", include={"id", "name", "channel_id", "subscribers", "niche", "wallet", "avatar_url"}),
                "agent_url": reg.agent_url,
            }  # fmt: skip
            for reg in await state.ctx.registry.all()
        ]

    # ---- advertiser: campaigns -----------------------------------------------------

    @app.post("/api/campaigns", status_code=201)
    async def create_campaign(body: CampaignCreate, user: AdvertiserUser) -> dict[str, Any]:
        if not user.wallet:
            raise HTTPException(409, "link your wallet first: it signs the Payment Mandates")
        campaign = await state.runner.start(CampaignSpec(**body.model_dump(), owner_id=user.id, payer=user.wallet))
        return campaign.model_dump(mode="json")

    @app.post("/api/campaigns/parse-brief")
    async def parse_brief(body: BriefIn, _: AdvertiserUser) -> dict[str, Any]:
        """LLM drafts the campaign form from free text. The draft is validated, and the human submits it."""
        if state.ctx.llm is None:
            raise HTTPException(400, "Set ANTHROPIC_API_KEY to parse briefs; fill the form manually otherwise")
        draft = await state.ctx.llm.with_structured_output(CampaignCreate, method="json_schema").ainvoke(
            f"Turn this advertiser brief into campaign parameters. Use USD with 2 decimals. Brief:\n{body.brief}"
        )
        return CampaignCreate.model_validate(draft.model_dump()).model_dump(mode="json")

    @app.get("/api/campaigns")
    async def list_campaigns(who: Principal) -> list[dict[str, Any]]:
        campaigns = await state.ctx.store.list_campaigns()
        return [c.model_dump(mode="json") for c in campaigns if who == OPERATOR or (isinstance(who, User) and c.spec.owner_id == who.id)]

    @app.get("/api/campaigns/{campaign_id}")
    async def get_campaign(campaign_id: str, who: Principal) -> dict[str, Any]:
        c = await readable_campaign(campaign_id, who)
        pending = await state.runner.pending_approval(campaign_id)
        signing = []
        if pending:
            for deal_id in pending["deals"]:
                d = await state.deals.get(deal_id)
                signing.append({"deal_id": d.id, "influencer_id": d.influencer_id, "amount": str(d.terms.cap) if d.terms else None,
                                "typed_data": state.deals.typed_data(d), "payment_url": d.payment_url})  # fmt: skip
        return {**c.model_dump(mode="json"), "pending_approval": pending, "signing_requests": signing}

    @app.get("/api/campaigns/{campaign_id}/deals")
    async def campaign_deals(campaign_id: str, who: Principal) -> list[dict[str, Any]]:
        await readable_campaign(campaign_id, who)
        return [d.model_dump(mode="json") for d in await state.ctx.store.list_deals(campaign_id)]

    @app.get("/api/campaigns/{campaign_id}/events")
    async def campaign_events(campaign_id: str, who: Principal) -> list[dict[str, Any]]:
        await readable_campaign(campaign_id, who)
        return [e.model_dump(mode="json") for e in await state.ctx.store.list_events(campaign_id)]

    @app.post("/api/campaigns/{campaign_id}/decision")
    async def decide(campaign_id: str, body: Decision, user: AdvertiserUser) -> dict[str, str]:
        await readable_campaign(campaign_id, user)
        pending = await state.runner.pending_approval(campaign_id)
        if pending is None:
            raise HTTPException(409, "campaign is not waiting for approval")
        if body.approved and state.ctx.gateway.agent_signing:
            for deal_id in pending["deals"]:
                sig = body.signatures.get(deal_id)
                if sig is None or not state.deals.signature_valid(await state.deals.get(deal_id), sig):
                    raise HTTPException(400, f"missing or invalid wallet signature for {deal_id}")
        await state.runner.decide(campaign_id, body.approved, body.signatures)
        return {"status": "resumed"}

    # ---- deals -------------------------------------------------------------------------

    @app.get("/api/deals/{deal_id}")
    async def get_deal(deal_id: str, who: Principal) -> dict[str, Any]:
        return (await readable_deal(deal_id, who)).model_dump(mode="json")

    @app.get("/api/deals/{deal_id}/events")
    async def deal_events(deal_id: str, who: Principal) -> list[dict[str, Any]]:
        deal = await readable_deal(deal_id, who)
        return [e.model_dump(mode="json") for e in await state.ctx.store.list_events(deal.campaign_id, deal_id)]

    @app.post("/api/deals/{deal_id}/proof")
    async def submit_proof(deal_id: str, body: ProofIn, user: InfluencerUser) -> dict[str, Any]:
        deal = await state.deals.get(deal_id)
        if deal.influencer_id != user.id:
            raise HTTPException(404, "deal not found")
        return (await state.deals.submit_proof(deal_id, body.video_url)).model_dump(mode="json")

    @app.post("/api/deals/{deal_id}/dispute")
    async def dispute(deal_id: str, body: DisputeIn, user: AdvertiserUser) -> dict[str, Any]:
        await readable_deal(deal_id, user)
        return (await state.deals.dispute(deal_id, body.reason)).model_dump(mode="json")

    @app.get("/api/videos/{video}")
    async def video(video: str, _: Principal) -> dict[str, Any]:
        video_id = parse_video_id(video)
        if state.ctx.videos is None:
            raise HTTPException(503, "the verifier is offline: set YOUTUBE_API_KEY")
        info = await state.ctx.videos.get_video(video_id) if video_id else None
        if info is None:
            raise HTTPException(404, "video not found")
        return info.model_dump(mode="json")

    # ---- influencer workspace ----------------------------------------------------------

    @app.get("/api/influencer/deals")
    async def influencer_deals(user: InfluencerUser) -> list[dict[str, Any]]:
        out = []
        for d in await state.ctx.store.list_deals():
            if d.influencer_id != user.id:
                continue
            spec = (await state.deals.campaign(d.campaign_id)).spec
            brand = spec.model_dump(mode="json", include={"advertiser", "brand_handle", "niche", "deliverable", "brief", "required_tags"})
            out.append({**d.model_dump(mode="json"), "campaign": brand})
        return sorted(out, key=lambda d: d["updated_at"] or "", reverse=True)

    @app.get("/api/influencer/inbox")
    async def influencer_inbox(user: InfluencerUser) -> list[dict[str, Any]]:
        return list(reversed(state.hub.inbox(user.id)))

    # ---- live updates ---------------------------------------------------------------------

    @app.get("/api/stream")
    async def stream(request: Request, user: CurrentUser) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            async with state.ctx.bus.subscribe() as queue:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if user.id in event.pop("_audience", set()):
                        yield f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- HSP webhook --------------------------------------------------------------

    @app.post("/webhooks/hsp")
    async def hsp_webhook(request: Request) -> dict[str, int]:
        raw = await request.body()
        if not await state.deals.handle_webhook(raw, request.headers.get("X-Signature")):
            raise HTTPException(401, "bad signature")
        return {"code": 0}

    @app.get("/checkout/simulated/{payment_request_id}", response_class=HTMLResponse, include_in_schema=False)
    async def simulated_checkout(payment_request_id: str) -> str:
        return (
            "<!doctype html><meta charset=utf-8><title>Simulated checkout</title><body style='font-family:system-ui;padding:2rem'>"
            f"<h1>Simulated HSP checkout</h1><p>Payment request <code>{payment_request_id}</code>.</p>"
            "<p>In simulated mode the advertiser signs the EIP-3009 Payment Mandate in their browser wallet from the campaign page. "
            "In hosted mode this link is HSP's real checkout.</p><a href='/app'>Back to ZyroXQ</a></body>"
        )

    # ---- assistant (MCP) ------------------------------------------------------------

    @app.post("/api/assistant")
    async def ask(body: Question, user: AdvertiserUser) -> dict[str, str]:
        if state.assistant is None:
            raise HTTPException(400, "Set ANTHROPIC_API_KEY to use the assistant")
        try:
            return {"answer": await state.assistant.ask(body.question, issue_user_token(settings.session_secret, user.id))}
        except Exception as e:  # LLM/provider errors become a readable message, not a 500
            log.exception("assistant failed")
            raise HTTPException(502, f"The assistant could not answer: {type(e).__name__}: {str(e)[:300]}") from e

    # ---- dev: time travel (simulated escrow only) ----------------------------------------

    @app.post("/api/dev/advance")
    async def dev_advance(body: AdvanceIn, user: CurrentUser) -> dict[str, Any]:
        if not state.ctx.escrow.simulated:
            raise HTTPException(400, "time travel only works with the simulated escrow (on-chain uses block time)")
        state.ctx.clock.advance(timedelta(hours=body.hours))
        actions = await state.deals.tick()
        log.info("%s advanced the clock by %sh (%d actions)", user.email, body.hours, actions)
        return {"clock_offset_hours": state.ctx.clock.offset_hours, "actions": actions}

    # ---- React app ------------------------------------------------------------------------

    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def spa(path: str) -> FileResponse | HTMLResponse:
        if path.split("/", 1)[0] in ("api", "auth", "webhooks", "a2a", "mcp"):
            raise HTTPException(404, "not found")
        file = (STATIC / path).resolve()
        if path and file.is_file() and STATIC.resolve() in file.parents:
            return FileResponse(file)
        if (STATIC / "index.html").is_file():
            return FileResponse(STATIC / "index.html")
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>ZyroXQ</title><body style='font-family:system-ui;padding:2rem'>"
            "<h1>ZyroXQ API is running</h1><p>The web app is not built yet. Run <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "or <code>npm run dev</code> and open <a href='http://localhost:5173'>http://localhost:5173</a>.</p></body>"
        )

    return app


async def _scheduler(state: State, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await state.deals.tick()
        except Exception:
            log.exception("scheduler tick failed")


__all__ = ["DealState", "create_app"]
