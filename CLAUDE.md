# ZyroXQ — HashKey "Agent Payments & A2A Settlement (HSP)" hackathon track

Agent-negotiated influencer deals with escrow-backed, proof-based payouts settled through the HashKey Merchant gateway (HSP).

## Product flow
1. **Advertiser Agent** (Payment Agent) discovers **Influencer Agents** (Receiving Agents) via A2A Agent Cards and negotiates deals in parallel.
2. Influencer Agent returns signed deal terms (deliverable, fixed fee, per-1K-view rate, cap, deadlines, proof rules).
3. ZyroXQ backend is the single onboarded HSP merchant: it turns accepted terms into a **Cart Mandate** (`pay_to` = CampaignEscrow, once confirmed allowed) and creates the order.
4. Advertiser Agent runs a deterministic policy check, then the wallet signs the **Payment Mandate** (EIP-712); HSP verifies and executes on-chain.
5. Verifier checks the post via platform API (YouTube first), signs an attestation; escrow releases fixed fee, then performance fee at T+7d, refunds the rest.

Core rule: **the LLM proposes, deterministic code decides, the key signs.** No payment path may depend on LLM output alone.

## Layout
Python 3.13 project managed with uv; package lives in `src/zyroxq/`.
- Planned modules: `zyroxq/hsp` (HSP merchant client: Canonical JSON, HMAC signing, ES256K merchant JWT with low-S, Cart Mandate builder, orders/payments, webhook verification), `zyroxq/agents/advertiser` and `zyroxq/agents/influencer` (LangGraph agents exposed over A2A), `zyroxq/policy` (deterministic payment policy engine), `zyroxq/verifier` (YouTube proof checks + attestations), `zyroxq/mcp` (MCP tool servers), `contracts/` (CampaignEscrow).
- `scripts/` — planned: `gen_merchant_key.py` (onboarding key pair → `keys/`), `smoke_create_order.py` (live QA order).

## Stack
- Agents: LangChain 1.x (`langchain.agents.create_agent`), LangGraph, LangSmith tracing (env `LANGSMITH_*`), `langchain-anthropic` as the LLM provider.
- Tools: MCP Python SDK (`mcp.server.fastmcp.FastMCP`) + `langchain-mcp-adapters` (`MultiServerMCPClient`).
- A2A: `a2a-sdk` 1.x — the 0.x `a2a.server.apps` module is gone; mount routes on FastAPI with `a2a.server.routes.fastapi_routes.add_a2a_routes_to_fastapi`, implement `a2a.server.agent_execution.AgentExecutor`.
- HTTP/API: FastAPI + uvicorn, httpx (tests mock with respx).
- Database: PostgreSQL via psycopg 3 (`psycopg[binary,pool]`), env `DATABASE_URL`; LangGraph state via `langgraph.checkpoint.postgres` (`PostgresSaver` / `AsyncPostgresSaver`, call `.setup()` once).
- Crypto: `cryptography` (secp256k1 ES256K), `eth-account` (EIP-712 Payment Mandate), `web3` (escrow contract).

## Commands
- `uv sync` — install (set `UV_LINK_MODE=copy` to silence the hardlink warning on Windows)
- `uv add <pkg>` / `uv add --dev <pkg>` — then regenerate the requirements files:
  `uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt`
  `uv export --format requirements-txt --no-hashes --only-dev --no-emit-project -o requirements-dev.txt`
- `uv run pytest` · `uv run ruff check .` · `uv run ruff format .`
- `pyproject.toml` + `uv.lock` are the source of truth; `requirements*.txt` are generated for pip users — never edit them by hand.

## HSP facts (from https://hashkey-merchant.github.io/#/ docs)
- QA (testnet) base: `https://merchant-qa.hashkeymerchant.com`, API prefix `/api/v1`.
- Merchant endpoints: `POST /merchant/orders`, `POST /merchant/orders/reusable`, `GET /merchant/payments`, `GET /merchant/payments/reusable`.
- HMAC message: `METHOD\nPATH\nQUERY\nsha256hex(canonicalBody)\ntimestamp\nnonce`; ±300s skew; nonce unique per 5 min. Send the canonical JSON string itself as the body so hashes match (Python: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).
- `merchant_authorization`: ES256K JWT, `aud = "HashkeyMerchant"`, `cart_hash` = sha256 of canonical `cart_mandate.contents`.
- All amounts in Cart Mandate are USD strings; `sum(display_items) == total` or error 10001.
- Payment method is x402 v2; HashKey Chain Testnet = `hashkey-testnet`, chain 133, USDC via EIP-3009.
- Webhook `X-Signature: t=..,v1=..`, HMAC of `${t}.${rawBody}` with app_secret; handlers must be idempotent on `request_id`.
- Terminal states: `payment-finalized`, `payment-failed`.

## Open questions for the HashKey team
1. Payer side (`/api/v1/payment/pay-mandate`, `flow/:id`) is not documented — can an agent sign the Payment Mandate programmatically instead of the hosted checkout? Exact EIP-712 payload?
2. May `pay_to` be a smart-contract (escrow) address?
3. QA gateway returns a Cloudflare 403 challenge to non-browser clients from our network — IP allowlist needed?
4. Testnet USDC faucet on HashKey Chain Testnet.

## Conventions
- Python ≥ 3.13, type hints throughout, pydantic v2 models for all protocol payloads, pytest (+ pytest-asyncio) for tests.
- Never commit `.env`, `keys/`, or `*.pem` (gitignored).
