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
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field, ValidationError, model_validator
from starlette.middleware.sessions import SessionMiddleware
from web3 import Web3

from ..agent_policy import AdvertiserPolicy, InfluencerPolicy, creator_evidence
from ..agent_policy import rules as pricing_rules
from ..agent_policy.changes import material_changes
from ..agents.advertiser.assistant import Assistant
from ..agents.advertiser.runner import CampaignRunner
from ..agents.influencer.hub import InfluencerHub
from ..agents.influencer.niche import NICHES, analyze_niches
from ..auth.crypto import new_agent_key
from ..auth.google import GoogleOAuth, OAuthError
from ..auth.tokens import issue_user_token, read_user_token
from ..auth.wallet_link import link_message
from ..clock import Clock
from ..compliance import DISCLAIMER, campaign_problems
from ..config import Settings, get_settings
from ..feedback import FeedbackIn, LessonUpdate, build_feedback, performance_row, performance_summary
from ..mcp_servers.server import build_mcp
from ..models import DEFAULT_MAX_ROUNDS, Campaign, CampaignSpec, CompensationModel, ContentCheck, ContentScope, Deal, DealState, RateCard, User, new_id
from ..services.context import AppContext, build_context, chat_model
from ..services.deals import ADVERTISER_PRIVATE_EVENTS, AUTH_EXPIRED, POLICY_KIND, DealError, DealService
from ..verifier.channels import summarize
from ..verifier.content import suggest_checks
from ..verifier.youtube import parse_video_id
from ..wallet.signer import recover_personal_sign

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"  # the built React app (web/ → npm run build)
MAX_NICHES = 5
NICHE_SAMPLE = 25  # recent uploads read for the niche analysis
OPERATOR = "operator"  # read-only principal for the stdio MCP server
MAX_REQUIREMENTS_CHARS = 15_000  # about three pages of brief
SHORTS_MAX_SECONDS = 180


class CampaignCreate(BaseModel):
    advertiser: str = Field(min_length=1, max_length=80)
    brand_handle: str = Field(pattern=r"^@[A-Za-z0-9_.]{2,40}$")
    niche: str = Field(default="fitness", min_length=2, max_length=40)
    budget: Decimal = Field(gt=0, le=10_000)
    max_creators: int = Field(default=3, ge=1, le=10)
    min_subscribers: int = Field(default=0, ge=0)
    max_subscribers: int = Field(default=10_000_000, ge=1)
    max_fixed: Decimal = Field(ge=0)
    max_cpm: Decimal = Field(ge=0)
    cap_per_creator: Decimal = Field(gt=0)
    compensation_model: CompensationModel = "hybrid"
    scope: ContentScope = Field(default_factory=ContentScope)
    category: str = Field(default="", max_length=60)
    jurisdictions: list[str] = Field(default_factory=lambda: ["IN", "US"], max_length=5)
    max_rounds: int = Field(default=DEFAULT_MAX_ROUNDS, ge=1, le=8)
    review_required: bool = False
    required_tags: list[str] = Field(default_factory=lambda: ["#ad"], min_length=1, max_length=5)
    publish_within_days: int = Field(default=7, ge=1, le=60)
    brief: str = Field(default="", max_length=2000)
    deliverable: Literal["youtube_short", "youtube_video"] = "youtube_short"
    min_duration_seconds: int = Field(default=0, ge=0, le=4 * 3600)
    max_duration_seconds: int = Field(default=0, ge=0, le=4 * 3600)  # 0 = no length rule
    requirements: str = Field(default="", max_length=MAX_REQUIREMENTS_CHARS)
    content_checks: list[ContentCheck] = Field(default_factory=list, max_length=6)  # checked in the video by the content agent

    @model_validator(mode="after")
    def bounds(self) -> CampaignCreate:
        if len({c.id for c in self.content_checks}) != len(self.content_checks):
            raise ValueError("content check ids must be unique")
        if self.max_duration_seconds and self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError("minimum video length cannot exceed the maximum")
        if self.deliverable == "youtube_short" and self.max_duration_seconds > SHORTS_MAX_SECONDS:
            raise ValueError(f"YouTube Shorts are at most {SHORTS_MAX_SECONDS} seconds")
        if self.max_fixed > self.cap_per_creator:
            raise ValueError("max_fixed cannot exceed cap_per_creator")
        if self.compensation_model == "fixed" and (self.max_fixed <= 0 or self.max_cpm != 0):
            raise ValueError("a fixed-fee campaign needs max_fixed > 0 and max_cpm = 0")
        if self.compensation_model == "cpm" and (self.max_cpm <= 0 or self.max_fixed != 0):
            raise ValueError("a CPM campaign needs max_cpm > 0 and max_fixed = 0")
        if self.compensation_model == "hybrid" and self.max_fixed <= 0:
            raise ValueError("a hybrid campaign needs max_fixed > 0")
        self.jurisdictions = sorted({j.strip().upper() for j in self.jurisdictions})
        if self.cap_per_creator > self.budget:
            raise ValueError("cap_per_creator cannot exceed the budget")
        for v in (self.budget, self.max_fixed, self.max_cpm, self.cap_per_creator):
            if v != v.quantize(Decimal("0.01")):
                raise ValueError("amounts must have at most 2 decimals")
        return self


class ChecksSuggestIn(BaseModel):
    requirements: str = Field(default="", max_length=MAX_REQUIREMENTS_CHARS)
    brand: str = Field(min_length=1, max_length=80)


class Decision(BaseModel):
    approved: bool
    signatures: dict[str, str] = Field(default_factory=dict)  # deal_id → EIP-712 signature from the payer's wallet


class ProofIn(BaseModel):
    video_url: str = Field(max_length=300)
    attestations: dict[str, bool] = Field(default_factory=dict, max_length=10)


class PolicyIn(BaseModel):
    policy: dict[str, Any]
    confirm_material_change: bool = False


class ReviewIn(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=500)


class DisputeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdvanceIn(BaseModel):
    hours: float = Field(gt=0, le=24 * 30)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class BriefIn(BaseModel):
    brief: str = Field(min_length=10, max_length=2000)


class BriefDraft(BaseModel):
    """What the LLM fills from a brief. Kept flat and unconstrained so the structured-output schema stays small;
    CampaignCreate applies the real limits afterwards."""

    advertiser: str = Field(description="brand or company name")
    brand_handle: str = Field(description="the brand's handle starting with @, e.g. @acme")
    niche: str = Field(description="creator niche, one or two words, e.g. fitness")
    budget: float = Field(description="total campaign budget in USD")
    max_creators: int
    compensation_model: Literal["fixed", "cpm", "hybrid"]
    max_fixed: float = Field(description="highest fixed fee per creator in USD; 0 for a pure CPM campaign")
    max_cpm: float = Field(description="highest USD paid per 1,000 views; 0 for a fixed-fee campaign")
    cap_per_creator: float = Field(description="most one creator can earn in USD, at least max_fixed")
    deliverable: Literal["youtube_short", "youtube_video"]
    publish_within_days: int
    requirements: str = Field(description="what the video must show or say, in the brief's words")


class AdvertiserProfileIn(BaseModel):
    company: str = Field(min_length=1, max_length=80)
    brand_handle: str = Field(pattern=r"^@[A-Za-z0-9_.]{2,40}$")


class InfluencerProfileIn(BaseModel):
    niche: str = Field(default="", max_length=40)  # legacy single niche; `niches` wins when given
    niches: list[str] = Field(default_factory=list, max_length=MAX_NICHES)  # primary first
    fixed_min: Decimal = Field(gt=0, le=100_000)
    cpm_min: Decimal = Field(ge=0, le=1_000)
    preferred_fixed: Decimal = Field(gt=0, le=100_000)
    persona: str = Field(default="", max_length=500)
    agent_active: bool = True

    @model_validator(mode="after")
    def anchor(self) -> InfluencerProfileIn:
        if self.preferred_fixed < self.fixed_min:
            raise ValueError("preferred_fixed must be at least fixed_min")
        cleaned = list(dict.fromkeys(n.strip().lower() for n in (self.niches or [self.niche]) if n.strip()))
        if not cleaned:
            raise ValueError("choose at least one niche")
        if any(not 2 <= len(n) <= 40 for n in cleaned):
            raise ValueError("each niche must be 2 to 40 characters")
        self.niches, self.niche = cleaned, cleaned[0]
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

    from ..store.postgres import open_pool

    pool = await open_pool(settings.database_url, max_size=5, autocommit=True, prepare_threshold=0, row_factory=dict_row)
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
    state.hub = InfluencerHub(lambda i: state.ctx.registry.identity(i), lambda i: state.ctx.registry.agent_url(i), clock, llm,
                              record=lambda r: state.deals.record_decision(r), resolve_disclosure=lambda i: state.ctx.disclosure.resolve(i))  # fmt: skip

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
            await state.runner.recover()  # campaigns a previous process left mid-negotiation continue with their next creator
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
                await state.runner.shutdown()
                if hasattr(state.ctx.store, "close"):
                    await state.ctx.store.close()

    app.mount("/a2a/influencers", state.hub)
    app.mount("/mcp", mcp.streamable_http_app())

    @app.exception_handler(DealError)
    async def deal_error(_: Request, e: DealError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, e: psycopg.OperationalError) -> JSONResponse:
        # A connection dropped mid-query (the pool replaces it): nothing was half-applied that a retry would repeat badly
        log.warning("database unavailable on %s %s: %s", request.method, request.url.path, str(e).splitlines()[0] if str(e) else type(e).__name__)
        return JSONResponse({"detail": "The database is briefly unreachable. Please try again in a moment."}, status_code=503, headers={"Retry-After": "5"})

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

    # Browsers sometimes deliver the callback twice at once (prefetch, a double click, a retried redirect). The session
    # cookie is rewritten by every response, so a losing duplicate used to overwrite the winner's signed-in cookie and
    # bounce the user back to /login. Each OAuth state now runs once; every request carrying it shares the result.
    sign_ins: dict[str, asyncio.Task[tuple[str | None, str]]] = {}

    async def sign_in(pending: dict[str, Any], code: str) -> tuple[str | None, str]:
        """(user id, "") on success, (None, reason) otherwise."""
        try:
            tokens = await oauth.exchange(code)
            info = await oauth.userinfo(tokens["access_token"])
            user = await state.ctx.store.get_user_by_email(info["email"])
            role = user.role if user else pending["role"]
            channel = await oauth.my_channel(tokens["access_token"], tokens.get("scope")) if role == "influencer" and pending["role"] == "influencer" else None
        except OAuthError as e:
            return None, str(e)
        if user is None:
            if role == "influencer" and channel is None:
                return None, "influencers must sign in with the Influencer button to connect YouTube"
            user = User(id=new_id("USR"), email=info["email"], name=info.get("name", info["email"]), picture=info.get("picture", ""),
                        role=role, created_at=state.ctx.clock.now())  # fmt: skip
            if role == "influencer":
                user.agent_signer, user.agent_key_encrypted = new_agent_key(settings.session_secret)
            user = await state.ctx.store.create_user(user)  # another sign-in for the same email may have created it first
        user.name, user.picture = info.get("name", user.name), info.get("picture", user.picture)
        if channel is not None:
            if user.channel and user.channel.channel_id != channel.channel_id:
                user.delegation = user.delegation_signature = ""  # the wallet must re-authorize the agent for the new channel
            user.channel = channel
        await state.ctx.store.save_user(user)
        return user.id, ""

    def shared_sign_in(oauth_state: str, pending: dict[str, Any], code: str) -> asyncio.Task[tuple[str | None, str]]:
        task = sign_ins.get(oauth_state)
        if task is None:
            task = sign_ins[oauth_state] = asyncio.create_task(sign_in(pending, code))
            asyncio.get_running_loop().call_later(120, sign_ins.pop, oauth_state, None)
        return task

    @app.get("/auth/google/callback", include_in_schema=False)
    async def google_callback(request: Request, code: str = "", error: str = "") -> RedirectResponse:
        oauth_state = request.query_params.get("state", "")
        pending = request.session.get("oauth")

        def failed(reason: str) -> RedirectResponse:
            log.info("google sign-in sent back to /login: %s", reason)
            if request.session.get("uid"):  # already signed in (an earlier callback finished): keep that session
                return to_frontend("/app")
            return to_frontend("/login", reason)

        if error or not code:
            return failed(f"Google sign-in was cancelled ({error or 'no code'})")
        if oauth_state in sign_ins:  # a duplicate of a callback that is running or just finished
            task = sign_ins[oauth_state]
        elif pending and secrets.compare_digest(pending["state"], oauth_state):
            task = shared_sign_in(oauth_state, pending, code)
        elif pending and pending["state"] in sign_ins:  # a stale flow while the browser's current one is completing
            task = sign_ins[pending["state"]]
        else:
            return failed("sign-in expired, please try again")
        uid, problem = await asyncio.shield(task)
        if uid is None:
            return failed(problem)
        request.session.clear()
        request.session["uid"] = uid
        return to_frontend("/app")

    @app.post("/auth/logout")
    async def logout(request: Request) -> dict[str, str]:
        request.session.clear()
        return {"status": "signed out"}

    # ---- account + onboarding ---------------------------------------------------

    def channels_online() -> Any:
        if state.ctx.channels is None:
            raise HTTPException(503, "YouTube lookups are offline: set YOUTUBE_API_KEY")
        return state.ctx.channels

    async def youtube_call(call: Any) -> Any:
        try:
            return await call
        except httpx.HTTPStatusError as e:
            raise HTTPException(502, f"YouTube Data API returned {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise HTTPException(502, f"YouTube Data API unreachable: {type(e).__name__}") from e

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
        user.niche, user.niches = body.niche, body.niches
        suggested = {s.niche for s in user.niche_analysis.suggestions} if user.niche_analysis else set()
        user.niche_source = "youtube_analysis" if suggested and set(body.niches) <= suggested else "manual"
        user.rate_card = RateCard(fixed_min=body.fixed_min, cpm_min=body.cpm_min, preferred_fixed=body.preferred_fixed)
        user.persona = body.persona.strip()
        user.agent_active = body.agent_active
        await state.ctx.store.save_user(user)
        doc = await state.ctx.store.get_version(POLICY_KIND, user.id)
        if doc and doc.get("role") == "influencer":  # the agent runs on the saved policy: carry the edited rates into a new version
            policy = InfluencerPolicy.model_validate(doc)
            policy.hard.min_fixed_fee, policy.hard.min_cpm = body.fixed_min, body.cpm_min
            policy.preferences.preferred_fixed_fee, policy.persona = body.preferred_fixed, user.persona
            if policy.preferences.preferred_cpm is not None and policy.preferences.preferred_cpm < body.cpm_min:
                policy.preferences.preferred_cpm = body.cpm_min
            await state.ctx.store.save_version(POLICY_KIND, user.id, policy.model_dump(mode="json", exclude={"version", "updated_at"}))
        return me_payload(user)

    @app.get("/api/niches")
    async def niches() -> dict[str, Any]:
        """The taxonomy the channel analysis suggests from. Creators may also type their own niche."""
        return {"niches": list(NICHES), "max_per_creator": MAX_NICHES}

    @app.post("/api/me/niche-analysis")
    async def analyze_my_niches(user: InfluencerUser) -> dict[str, Any]:
        """Read the creator's public channel and recent uploads, suggest niches and keep the analysis on the account.
        It changes nothing the agent uses: the creator saves the niches they choose with PUT /api/me/influencer."""
        if user.channel is None:
            raise HTTPException(409, "connect your YouTube channel first")
        source = channels_online()
        details = await youtube_call(source.channel(user.channel.channel_id))
        if details is None:
            raise HTTPException(404, "YouTube returned no channel for your account")
        recent = await youtube_call(source.recent_videos(details.channel_id, NICHE_SAMPLE))
        analysis = await analyze_niches(details, recent, state.ctx.clock.now(), state.ctx.llm)
        user.niche_analysis = analysis
        await state.ctx.store.save_user(user)
        return analysis.model_dump(mode="json")

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

    @app.get("/api/me/wallet/balance")
    async def wallet_balance(user: CurrentUser) -> dict[str, Any]:
        """Advisory USDC balance of the linked wallet. It can change before settlement and decides nothing."""
        if not user.wallet:
            raise HTTPException(409, "link a wallet first")
        ctx = state.ctx
        out: dict[str, Any] = {"wallet": user.wallet, "token": ctx.token.coin, "token_address": ctx.token.contract_address,
                               "chain_id": ctx.token.chain_id, "decimals": ctx.token.decimals, "checked_at": ctx.clock.now().isoformat(),
                               "settlement_simulated": ctx.gateway.simulated}  # fmt: skip
        try:
            units = await asyncio.wait_for(asyncio.to_thread(ctx.balances.balance_of, user.wallet), 15)
        except Exception as e:  # noqa: BLE001 - RPC down: the panel shows "unknown", signing is still allowed
            return {**out, "balance": None, "units": None, "error": f"could not read the balance ({type(e).__name__})"}
        return {**out, "balance": str(Decimal(units) / (Decimal(10) ** ctx.token.decimals)), "units": str(units), "error": None}

    # ---- platform ------------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        ctx = state.ctx
        return {
            "settlement_mode": ctx.gateway.name,
            "settlement_simulated": ctx.gateway.simulated or ctx.escrow.simulated,
            "settlement_min_confirmations": settings.settlement_min_confirmations,
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
                **reg.profile.model_dump(mode="json", include={"id", "name", "channel_id", "subscribers", "niche", "niches", "wallet", "avatar_url"}),
                "agent_url": reg.agent_url,
            }  # fmt: skip
            for reg in await state.ctx.registry.all()
        ]

    # ---- advertiser: campaigns -----------------------------------------------------

    @app.post("/api/campaigns", status_code=201)
    async def create_campaign(body: CampaignCreate, user: AdvertiserUser, request: Request) -> dict[str, Any]:
        if not user.wallet:
            raise HTTPException(409, "link your wallet first: it signs the Payment Mandates")
        # Disclosure gate: every jurisdiction needs a policy and a required tag that policy approves
        policies = {j: state.ctx.disclosure.latest(j) for j in body.jurisdictions}
        problems = campaign_problems(policies, body.required_tags)
        if problems:
            raise HTTPException(400, "; ".join(problems))
        latest = await state.ctx.store.get_version(POLICY_KIND, user.id)
        snapshot = {
            "disclosure_policies": [p.id for p in policies.values() if p],
            "agent_policy_version": latest["version"] if latest and latest.get("role") == "advertiser" else 0,
        }
        # A double-click or a retried request carries the same Idempotency-Key: it gets the campaign the first one started
        key = request.headers.get("idempotency-key", "").strip()[:128]
        spec_id = new_id("CMP")
        if key and (holder := await state.ctx.store.claim("campaign_start", f"{user.id}:{key}", spec_id)) != spec_id:
            for _ in range(50):  # the first request may still be saving it
                if (existing := await state.ctx.store.get_campaign(holder)) is not None:
                    return existing.model_dump(mode="json")
                await asyncio.sleep(0.1)
            raise HTTPException(409, "this campaign is already being started")
        campaign = await state.runner.start(CampaignSpec(**body.model_dump(), **snapshot, id=spec_id, owner_id=user.id, payer=user.wallet))
        return campaign.model_dump(mode="json")

    @app.get("/api/campaigns/{campaign_id}/negotiations")
    async def campaign_negotiations(campaign_id: str, user: AdvertiserUser) -> dict[str, Any]:
        """The creator queue: one session per creator in the order they are negotiated with, plus campaign progress.
        What a refreshed page restores; each conversation's messages come from /api/negotiations/{id}/events."""
        await readable_campaign(campaign_id, user)
        return {"progress": await state.deals.progress(campaign_id), "sessions": await state.deals.negotiation_sessions(campaign_id)}

    @app.post("/api/campaigns/{campaign_id}/negotiations/cancel")
    async def cancel_campaign_negotiations(campaign_id: str, user: AdvertiserUser) -> dict[str, Any]:
        """Stops the creator queue: no further creator is contacted and the running negotiation stops before its next message."""
        campaign = await readable_campaign(campaign_id, user)
        if campaign.negotiation_cancelled:
            return await state.deals.progress(campaign_id)
        return await state.deals.cancel_campaign_negotiations(campaign, user.id)

    @app.post("/api/campaigns/content-checks/suggest")
    async def suggest_content_checks(body: ChecksSuggestIn, _: AdvertiserUser) -> dict[str, Any]:
        """Checks the content agent can run in the posted video, read from the brief. The advertiser edits and confirms them."""
        checks, method = await suggest_checks(body.requirements, body.brand, state.ctx.llm)
        return {"checks": [c.model_dump(mode="json") for c in checks], "method": method,
                "caption_reader": state.ctx.captions is not None, "vision": state.ctx.llm is not None}  # fmt: skip

    @app.post("/api/campaigns/parse-brief")
    async def parse_brief(body: BriefIn, _: AdvertiserUser) -> dict[str, Any]:
        """LLM drafts the campaign form from free text. The draft is validated, and the human submits it."""
        if state.ctx.llm is None:
            raise HTTPException(400, "Set ANTHROPIC_API_KEY to parse briefs; fill the form manually otherwise")
        try:
            draft = await state.ctx.llm.with_structured_output(BriefDraft, method="json_schema").ainvoke(
                f"Turn this advertiser brief into campaign parameters. Use USD with 2 decimals. Brief:\n{body.brief}"
            )
        except Exception as e:  # provider/network errors: the form can still be filled by hand
            log.warning("brief parsing failed: %s", e)
            raise HTTPException(502, "Could not read the brief right now; fill the form manually") from e
        data: dict[str, Any] = draft.model_dump()
        for k in ("budget", "max_fixed", "max_cpm", "cap_per_creator"):
            data[k] = Decimal(str(data[k])).quantize(Decimal("0.01"))
        handle = "".join(ch for ch in data["brand_handle"] if ch.isalnum() or ch in "_.")
        data["brand_handle"] = f"@{handle[:40]}"
        data["niche"] = data["niche"].strip().lower()[:40]
        data["requirements"] = data["requirements"][:MAX_REQUIREMENTS_CHARS]
        data["brief"] = body.brief
        try:
            return CampaignCreate.model_validate(data).model_dump(mode="json")
        except ValidationError as e:
            raise HTTPException(422, "; ".join(err["msg"] for err in e.errors())) from e

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
                drafted = d.state == DealState.AWAITING_APPROVAL and state.ctx.gateway.agent_signing
                problems = await state.deals.authorization_problems(d) if drafted else []
                signing.append({"deal_id": d.id, "influencer_id": d.influencer_id, "amount": str(d.terms.cap) if d.terms else None,
                                "state": d.state, "typed_data": state.deals.typed_data(d) if d.state == DealState.AWAITING_APPROVAL else None,
                                "payment_url": d.payment_url, "problems": problems, "expired": AUTH_EXPIRED in problems,
                                "valid_before": int(d.mandate_draft["validBefore"]) if d.mandate_draft else None})  # fmt: skip
        funding = await state.deals.funding_summary(campaign_id)
        return {**c.model_dump(mode="json"), "pending_approval": pending, "signing_requests": signing, "funding": funding}

    @app.post("/api/campaigns/{campaign_id}/redraft")
    async def redraft(campaign_id: str, user: AdvertiserUser) -> dict[str, Any]:
        """Fresh authorizations (new expiry and nonce) for unsigned deals whose draft expired."""
        await readable_campaign(campaign_id, user)
        pending = await state.runner.pending_approval(campaign_id)
        if pending is None:
            raise HTTPException(409, "campaign is not waiting for approval")
        redrafted = []
        for deal_id in pending["deals"]:
            d = await state.deals.get(deal_id)
            if d.state == DealState.AWAITING_APPROVAL and AUTH_EXPIRED in await state.deals.authorization_problems(d):
                await state.deals.redraft(d)
                redrafted.append(deal_id)
        return {"redrafted": redrafted}

    @app.get("/api/campaigns/{campaign_id}/deals")
    async def campaign_deals(campaign_id: str, who: Principal) -> list[dict[str, Any]]:
        await readable_campaign(campaign_id, who)
        return [d.model_dump(mode="json") for d in await state.ctx.store.list_deals(campaign_id)]

    @app.get("/api/campaigns/{campaign_id}/events")
    async def campaign_events(campaign_id: str, who: Principal) -> list[dict[str, Any]]:
        await readable_campaign(campaign_id, who)
        events = await state.ctx.store.list_events(campaign_id)
        return [e.model_dump(mode="json") for e in events if who == OPERATOR or e.kind != "influencer_decision"]

    @app.post("/api/campaigns/{campaign_id}/decision")
    async def decide(campaign_id: str, body: Decision, user: AdvertiserUser) -> dict[str, str]:
        await readable_campaign(campaign_id, user)
        pending = await state.runner.pending_approval(campaign_id)
        if pending is None:
            raise HTTPException(409, "campaign is not waiting for approval")
        if body.approved and state.ctx.gateway.agent_signing:
            for deal_id in pending["deals"]:
                deal = await state.deals.get(deal_id)
                problems = await state.deals.authorization_problems(deal)
                if problems == [AUTH_EXPIRED]:
                    raise HTTPException(409, f"the authorization for {deal_id} expired: redraft and sign again")
                if problems:
                    raise HTTPException(400, f"{deal_id} cannot be paid: {'; '.join(problems)}")
                sig = body.signatures.get(deal_id)
                if sig is None or not state.deals.signature_valid(deal, sig):
                    raise HTTPException(400, f"missing or invalid wallet signature for {deal_id}")
        for deal_id in pending["deals"]:
            await state.deals.record_approval(deal_id, "payment", user.id, body.approved)
        await state.runner.decide(campaign_id, body.approved, body.signatures)
        return {"status": "resumed"}

    # ---- deals -------------------------------------------------------------------------

    @app.get("/api/deals/{deal_id}")
    async def get_deal(deal_id: str, who: Principal) -> dict[str, Any]:
        return (await readable_deal(deal_id, who)).model_dump(mode="json")

    @app.get("/api/deals/{deal_id}/events")
    async def deal_events(deal_id: str, who: Principal) -> list[dict[str, Any]]:
        deal = await readable_deal(deal_id, who)
        events = await state.ctx.store.list_events(deal.campaign_id, deal_id)
        if isinstance(who, User) and who.role == "influencer":  # the advertiser's limits and reasoning stay with the advertiser
            events = [e for e in events if e.kind not in ADVERTISER_PRIVATE_EVENTS]
        elif isinstance(who, User):
            events = [e for e in events if e.kind != "influencer_decision"]
        return [e.model_dump(mode="json") for e in events]

    def party_of(who: User | str) -> str | None:
        return None if who == OPERATOR else ("influencer" if isinstance(who, User) and who.role == "influencer" else "advertiser")

    @app.get("/api/deals/{deal_id}/decisions")
    async def deal_decisions(deal_id: str, who: Principal) -> list[dict[str, Any]]:
        """Each party sees only its own agent's decision records (the operator sees both)."""
        await readable_deal(deal_id, who)
        return await state.ctx.store.list_decisions(deal_id, party_of(who))

    @app.get("/api/deals/{deal_id}/negotiation")
    async def deal_negotiation(deal_id: str, who: Principal) -> dict[str, Any]:
        deal = await readable_deal(deal_id, who)
        spec = (await state.deals.campaign(deal.campaign_id)).spec
        return state.deals.negotiation_view(deal, spec)

    # ---- live negotiation: transcript replay, stream, cancel, feedback -------------------------
    # A negotiation is a deal's A2A exchange. It starts when the campaign launches (POST /api/campaigns) and runs once
    # in the campaign runner; these endpoints only read it, so a reconnecting browser can never start it again.

    def resume_after(request: Request, after: int) -> int:
        last = request.headers.get("last-event-id", "")
        return int(last) if last.isdigit() else after

    @app.get("/api/negotiations/{deal_id}/events")
    async def negotiation_events(deal_id: str, who: Principal, after: int = 0) -> list[dict[str, Any]]:
        """The stored transcript after `after`: what a refreshed page renders before the stream takes over."""
        await readable_deal(deal_id, who)
        return await state.ctx.store.list_negotiation_events(deal_id, after)

    @app.get("/api/negotiations/{deal_id}/stream")
    async def negotiation_stream(deal_id: str, request: Request, who: Principal, after: int = 0) -> StreamingResponse:
        """SSE: replays stored events after Last-Event-ID (or `after`), then pushes new ones live. Every frame carries
        `id: <event_id>`, so EventSource resumes where it stopped; duplicates are dropped by event_id. The stream
        closes after `negotiation_completed`."""
        deal = await readable_deal(deal_id, who)
        start = resume_after(request, after)

        def frame(e: dict[str, Any]) -> str:
            return f"id: {e['event_id']}\ndata: {json.dumps(e, default=str)}\n\n"

        async def events() -> AsyncIterator[str]:
            async with state.ctx.bus.subscribe() as queue:  # subscribe before reading the store, so nothing falls in between
                yield "retry: 3000\n\n"
                sent = start
                stored = await state.ctx.store.list_negotiation_events(deal_id, sent)
                for e in stored:
                    yield frame(e)
                    sent = e["event_id"]
                    if e["event_type"] == "negotiation_completed":
                        return
                if sent == 0 and not stored and deal.state != DealState.NEGOTIATING:
                    yield f"data: {json.dumps({'event_type': 'negotiation_legacy', 'negotiation_id': deal_id})}\n\n"  # ended before transcripts existed
                    return
                while not await request.is_disconnected():
                    try:
                        live = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if live.get("type") != "negotiation" or live.get("deal_id") != deal_id or live["event"]["event_id"] <= sent:
                        continue
                    # A gap means the queue overflowed: fill it from the store, in order
                    batch = [live["event"]] if live["event"]["event_id"] == sent + 1 else await state.ctx.store.list_negotiation_events(deal_id, sent)
                    for e in batch:
                        yield frame(e)
                        sent = e["event_id"]
                        if e["event_type"] == "negotiation_completed":
                            return

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/negotiations/{deal_id}/cancel")
    async def cancel_negotiation(deal_id: str, user: AdvertiserUser) -> dict[str, Any]:
        deal = await readable_deal(deal_id, user)
        if not await state.deals.cancel_negotiation(deal, user.id):
            raise HTTPException(409, "this negotiation has already ended")
        return {"status": "cancelling"}

    async def feedback_party(deal_id: str, user: User) -> tuple[Deal, str]:
        deal = await readable_deal(deal_id, user)
        return deal, ("influencer" if deal.influencer_id == user.id else "advertiser")

    @app.get("/api/negotiations/{deal_id}/feedback")
    async def my_negotiation_feedback(deal_id: str, user: CurrentUser) -> dict[str, Any] | None:
        deal, party = await feedback_party(deal_id, user)
        mine = [f for f in await state.ctx.store.list_feedback(deal_id=deal.id) if f["party"] == party]
        return {k: v for k, v in mine[-1].items() if k != "transcript"} if mine else None

    @app.post("/api/negotiations/{deal_id}/feedback", status_code=201)
    async def submit_negotiation_feedback(deal_id: str, body: FeedbackIn, user: CurrentUser) -> dict[str, Any]:
        """Each party rates its own agent once the negotiation ended. The extracted lesson waits for the owner's approval."""
        deal, party = await feedback_party(deal_id, user)
        if deal.state == DealState.NEGOTIATING:
            raise HTTPException(409, "the negotiation is still running")
        spec = (await state.deals.campaign(deal.campaign_id)).spec
        floors = None
        if party == "influencer" and (me := await state.ctx.registry.identity(user.id)) is not None:
            policy = me.policy or InfluencerPolicy.from_rate_card(me.profile.rate_card, me.persona)  # type: ignore[arg-type]
            floors = (policy.hard.min_fixed_fee, policy.hard.min_cpm)
        transcript = await state.ctx.store.list_negotiation_events(deal.id)
        record, lesson = build_feedback(body, deal=deal, spec=spec, role=party, user_id=user.id, transcript=transcript,  # type: ignore[arg-type]
                                        floors=floors, now=state.ctx.clock.now())  # fmt: skip
        for old in await state.ctx.store.list_feedback(deal_id=deal.id):  # resubmitting replaces the earlier lesson too
            if old["party"] == party and old.get("lesson_id") and (prev := await state.ctx.store.get_lesson(old["lesson_id"])):
                await state.ctx.store.save_lesson({**prev, "status": "dismissed", "reviewed_at": state.ctx.clock.now().isoformat()})
        await state.ctx.store.save_feedback(record)
        if lesson:
            await state.ctx.store.save_lesson(lesson)
        await state.deals.audit(deal.campaign_id, deal.id, "human", "negotiation_feedback", party=party, rating=body.rating, category=body.category)
        return {"feedback": {k: v for k, v in record.items() if k != "transcript"}, "lesson": lesson}

    @app.get("/api/me/agent-lessons")
    async def my_lessons(user: CurrentUser) -> list[dict[str, Any]]:
        return await state.ctx.store.list_lessons(user.id, user.role)

    @app.put("/api/me/agent-lessons/{lesson_id}")
    async def review_lesson(lesson_id: str, body: LessonUpdate, user: CurrentUser) -> dict[str, Any]:
        """The owner approves (optionally rewording) or dismisses a lesson. Only approved lessons reach the agent."""
        lesson = await state.ctx.store.get_lesson(lesson_id)
        if lesson is None or lesson["owner_id"] != user.id:
            raise HTTPException(404, "lesson not found")
        lesson = {**lesson, "status": body.status, "reviewed_at": state.ctx.clock.now().isoformat()}
        if body.guidance:
            lesson["guidance"] = body.guidance.strip()
        await state.ctx.store.save_lesson(lesson)
        return lesson

    @app.get("/api/me/agent-performance")
    async def my_agent_performance(user: CurrentUser) -> dict[str, Any]:
        """Negotiation metrics for the user's own agent, from stored negotiations and feedback only."""
        role = user.role
        rows = {r["deal_id"]: r for r in await state.ctx.store.list_performance(user.id, role)}
        # Negotiations that ended before agent_performance existed are added from the stored deals
        for c in await state.ctx.store.list_campaigns() if role == "advertiser" else []:
            if c.spec.owner_id == user.id:
                for d in await state.ctx.store.list_deals(c.spec.id):
                    if d.id not in rows and (row := performance_row(d, c.spec)):
                        await state.ctx.store.save_performance(row)
                        rows[d.id] = row
        if role == "influencer":
            for d in await state.ctx.store.list_deals():
                if d.influencer_id == user.id and d.id not in rows and (c := await state.ctx.store.get_campaign(d.campaign_id)):
                    if row := performance_row(d, c.spec):
                        await state.ctx.store.save_performance(row)
                        rows[d.id] = row
        feedback = [f for f in await state.ctx.store.list_feedback(user_id=user.id) if f["party"] == role]
        return performance_summary(list(rows.values()), feedback, role)

    @app.post("/api/deals/{deal_id}/compliance/review")
    async def compliance_review(deal_id: str, body: ReviewIn, user: AdvertiserUser) -> dict[str, Any]:
        await readable_deal(deal_id, user)
        return (await state.deals.compliance_review(deal_id, user.id, body.approved, body.note)).model_dump(mode="json")

    @app.get("/api/campaigns/{campaign_id}/compliance")
    async def campaign_compliance(campaign_id: str, who: Principal) -> dict[str, Any]:
        c = await readable_campaign(campaign_id, who)
        return await state.deals.compliance_view(c.spec)

    # ---- agent policies, evidence, disclosure, pricing ------------------------------------

    def _parse_policy(user: User, data: dict[str, Any]) -> InfluencerPolicy | AdvertiserPolicy:
        cls = InfluencerPolicy if user.role == "influencer" else AdvertiserPolicy
        try:
            return cls.model_validate({**data, "role": user.role, "version": 0, "updated_at": None})
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    async def _current_policy(user: User) -> tuple[InfluencerPolicy | AdvertiserPolicy | None, bool]:
        doc = await state.ctx.store.get_version(POLICY_KIND, user.id)
        if doc and doc.get("role") == user.role:
            return (InfluencerPolicy if user.role == "influencer" else AdvertiserPolicy).model_validate(doc), False
        if user.role == "influencer":
            return (InfluencerPolicy.from_rate_card(user.rate_card, user.persona) if user.rate_card else None), True
        return AdvertiserPolicy(), True

    @app.get("/api/me/agent-policy")
    async def get_agent_policy(user: CurrentUser) -> dict[str, Any]:
        policy, default = await _current_policy(user)
        return {"policy": policy.model_dump(mode="json") if policy else None, "is_default": default}

    @app.post("/api/me/agent-policy/validate")
    async def validate_agent_policy(body: PolicyIn, user: CurrentUser) -> dict[str, Any]:
        new = _parse_policy(user, body.policy)
        old, default = await _current_policy(user)
        return {"ok": True, "material_changes": [] if default else material_changes(old, new)}

    @app.put("/api/me/agent-policy")
    async def put_agent_policy(body: PolicyIn, user: CurrentUser) -> dict[str, Any]:
        """Saves a new immutable version. Loosening a hard constraint needs `confirm_material_change`.
        Running negotiations keep the version they started with."""
        new = _parse_policy(user, body.policy)
        old, default = await _current_policy(user)
        changes = [] if default else material_changes(old, new)
        if changes and not body.confirm_material_change:
            raise HTTPException(409, {"message": "these changes loosen hard constraints: confirm to save", "material_changes": changes})
        doc = await state.ctx.store.save_version(POLICY_KIND, user.id, new.model_dump(mode="json", exclude={"version", "updated_at"}))
        if isinstance(new, InfluencerPolicy):  # keep the onboarding rate card in step with the policy
            user.rate_card, user.persona = new.rate_card(), new.persona
            await state.ctx.store.save_user(user)
        return {"policy": doc, "is_default": False, "material_changes": changes}

    @app.get("/api/me/agent-policy/versions")
    async def agent_policy_versions(user: CurrentUser) -> list[dict[str, Any]]:
        return list(reversed(await state.ctx.store.list_versions(POLICY_KIND, user.id)))

    async def _evidence(user: User) -> list[dict[str, Any]]:
        policy, _ = await _current_policy(user)
        return [e.model_dump(mode="json") for e in creator_evidence(user, policy if isinstance(policy, InfluencerPolicy) else None)]

    @app.get("/api/me/evidence")
    async def my_evidence(user: InfluencerUser) -> list[dict[str, Any]]:
        return await _evidence(user)

    @app.get("/api/influencers/{influencer_id}/evidence")
    async def influencer_evidence(influencer_id: str, _: CurrentUser) -> dict[str, Any]:
        """What an advertiser may see: sourced metrics and the creator's public profile, never their limits."""
        if await state.ctx.registry.get(influencer_id) is None:
            raise HTTPException(404, "no listed influencer with this id")
        creator = await state.ctx.store.get_user(influencer_id)
        assert creator is not None
        policy, _ = await _current_policy(creator)
        public = policy.public_view() if isinstance(policy, InfluencerPolicy) else None
        return {"evidence": await _evidence(creator), "profile": public}

    @app.get("/api/disclosure-policies")
    async def disclosure_policies(_: Principal) -> dict[str, Any]:
        return {"policies": [p.model_dump(mode="json") for p in state.ctx.disclosure.all_latest()], "disclaimer": DISCLAIMER}

    @app.put("/api/disclosure-policies/{jurisdiction}")
    async def publish_disclosure_policy(jurisdiction: str, body: dict[str, Any], who: Principal) -> dict[str, Any]:
        """Operator only. Publishes a new version; existing campaigns keep the version they snapshotted."""
        if who != OPERATOR:
            raise HTTPException(403, "only the operator can publish disclosure policies")
        try:
            policy = await state.ctx.disclosure.publish(jurisdiction.upper(), body)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return policy.model_dump(mode="json")

    @app.get("/api/pricing/rules")
    async def pricing() -> dict[str, Any]:
        return pricing_rules()

    @app.post("/api/deals/{deal_id}/proof")
    async def submit_proof(deal_id: str, body: ProofIn, user: InfluencerUser) -> dict[str, Any]:
        deal = await state.deals.get(deal_id)
        if deal.influencer_id != user.id:
            raise HTTPException(404, "deal not found")
        return (await state.deals.submit_proof(deal_id, body.video_url, body.attestations)).model_dump(mode="json")

    @app.post("/api/deals/{deal_id}/dispute")
    async def dispute(deal_id: str, body: DisputeIn, user: AdvertiserUser) -> dict[str, Any]:
        await readable_deal(deal_id, user)
        return (await state.deals.dispute(deal_id, body.reason)).model_dump(mode="json")

    # ---- creator profiles and public YouTube data (read-only; also behind the MCP tools) -----

    async def _deal_summaries(influencer_id: str, who: User | str) -> list[dict[str, Any]]:
        """The caller's own deals with this creator (every deal for the operator or the creator)."""
        campaigns = {c.spec.id: c for c in await state.ctx.store.list_campaigns()}
        out = []
        for d in await state.ctx.store.list_deals():
            c = campaigns.get(d.campaign_id)
            if d.influencer_id != influencer_id or c is None:
                continue
            if not (who == OPERATOR or (isinstance(who, User) and who.id in (c.spec.owner_id, influencer_id))):
                continue
            paid = sum((p.amount for p in d.payouts if p.kind != "refund"), Decimal(0))
            terms = d.terms.model_dump(mode="json", include={"compensation_model", "fixed_fee", "cpm", "cap", "publish_by"}) if d.terms else None
            out.append({
                "deal_id": d.id,
                "campaign_id": c.spec.id,
                "campaign": f"{c.spec.advertiser} · {c.spec.niche}",
                "state": d.state,
                "terms": terms,
                "video_id": d.proof.video_id if d.proof else None,
                "views": d.proof.views if d.proof else None,
                "paid": str(paid),
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            })  # fmt: skip
        return out

    @app.get("/api/influencers/{influencer_id}/profile")
    async def influencer_profile(influencer_id: str, who: Principal, videos: int = 12) -> dict[str, Any]:
        """Everything an advertiser may know about a creator: public profile, the channel proved at sign-in,
        sourced evidence, live YouTube statistics and recent uploads, and the caller's own deals with them.
        Never the creator's floors, weights or private notes."""
        creator = await state.ctx.store.get_user(influencer_id)
        if creator is None or creator.role != "influencer":
            raise HTTPException(404, "no influencer with this id")
        reg = await state.ctx.registry.get(influencer_id)
        deals = await _deal_summaries(influencer_id, who)
        is_self = isinstance(who, User) and who.id == influencer_id
        if reg is None and not deals and not is_self and who != OPERATOR:
            raise HTTPException(404, "no listed influencer with this id")
        policy, _ = await _current_policy(creator)
        ch = creator.channel
        live: dict[str, Any] = {"available": False, "error": None, "channel": None, "recent_videos": [], "engagement": None}
        if ch is None:
            live["error"] = "no YouTube channel connected"
        elif state.ctx.channels is None:
            live["error"] = "YouTube lookups are offline: set YOUTUBE_API_KEY"
        else:
            try:
                details = await state.ctx.channels.channel(ch.channel_id)
                recent = await state.ctx.channels.recent_videos(ch.channel_id, max(1, min(videos, 50))) if details else []
            except httpx.HTTPError as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
                live["error"] = f"YouTube Data API error ({status})"
            else:
                live.update(
                    available=details is not None,
                    error=None if details else "YouTube returned no channel for this id",
                    channel=details.model_dump(mode="json") if details else None,
                    recent_videos=[v.model_dump(mode="json") for v in recent],
                    engagement=summarize(recent).model_dump(mode="json") if recent else None,
                )
        return {
            "influencer": {
                "id": creator.id,
                "name": ch.title if ch else creator.name,
                "avatar_url": ch.thumbnail_url if ch else creator.picture,
                "niche": creator.niche,
                "niches": creator.niches or ([creator.niche] if creator.niche else []),
                "niche_source": creator.niche_source,
                "niche_analysis": creator.niche_analysis.model_dump(mode="json") if creator.niche_analysis else None,
                "wallet": creator.wallet,
                "agent_signer": creator.agent_signer,
                "agent_active": creator.agent_active,
                "listed": reg is not None,
                "agent_url": reg.agent_url if reg else None,
                "member_since": creator.created_at.isoformat(),
            },
            "verified_channel": ch.model_dump(mode="json") if ch else None,
            "public_profile": policy.public_view() if isinstance(policy, InfluencerPolicy) else None,
            "evidence": await _evidence(creator),
            "youtube": live,
            "deals": deals,
        }

    @app.get("/api/channels/{channel}")
    async def channel_details(channel: str, _: Principal) -> dict[str, Any]:
        """Any public channel by id, @handle or URL."""
        details = await youtube_call(channels_online().channel(channel))
        if details is None:
            raise HTTPException(404, "channel not found")
        return details.model_dump(mode="json")

    @app.get("/api/channels/{channel}/videos")
    async def channel_videos(channel: str, _: Principal, limit: int = 12) -> dict[str, Any]:
        """Latest uploads with statistics, newest first, plus a derived engagement summary."""
        source = channels_online()
        details = await youtube_call(source.channel(channel))
        if details is None:
            raise HTTPException(404, "channel not found")
        recent = await youtube_call(source.recent_videos(details.channel_id, max(1, min(limit, 50))))
        videos = [v.model_dump(mode="json") for v in recent]
        return {"channel_id": details.channel_id, "videos": videos, "engagement": summarize(recent).model_dump(mode="json")}

    @app.get("/api/videos/{video}/stats")
    async def video_stats(video: str, _: Principal) -> dict[str, Any]:
        """Views, likes, comments and engagement rate of one video (None where YouTube hides them)."""
        video_id = parse_video_id(video)
        found = await youtube_call(channels_online().videos([video_id])) if video_id else []
        if not found:
            raise HTTPException(404, "video not found")
        return found[0].model_dump(mode="json")

    @app.get("/api/videos/{video}/comments")
    async def video_comments(video: str, _: Principal, limit: int = 20) -> dict[str, Any]:
        """Top-level comments by relevance; `enabled` is false when the creator turned comments off."""
        video_id = parse_video_id(video)
        if video_id is None:
            raise HTTPException(404, "video not found")
        return (await youtube_call(channels_online().comments(video_id, max(1, min(limit, 100))))).model_dump(mode="json")

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
            brand = spec.model_dump(
                mode="json",
                include={
                    "advertiser",
                    "brand_handle",
                    "niche",
                    "deliverable",
                    "brief",
                    "required_tags",
                    "requirements",
                    "min_duration_seconds",
                    "max_duration_seconds",
                    "content_checks",
                },
            )
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
    delay = interval
    while True:
        await asyncio.sleep(delay)
        try:
            await state.deals.tick()
            delay = interval
        except psycopg.OperationalError as e:  # database dropped or restarting: back off quietly, try again
            delay = min(delay * 2, 60.0)
            log.warning("scheduler: database unavailable (%s); next try in %.0fs", str(e).splitlines()[0] if str(e) else type(e).__name__, delay)
        except Exception:
            log.exception("scheduler tick failed")


__all__ = ["DealState", "create_app"]
