# ZyroXQ

> **Agents negotiate influencer deals, HSP locks the money, and verified proof of the post releases it.**

ZyroXQ is a decentralized influencer-marketing platform built for the HashKey **Agent Payments & A2A Settlement (HSP)** hackathon track. An advertiser's AI agent finds influencer agents and negotiates deals with many of them in parallel over **A2A**. Each payment is authorized with **HSP Cart and Payment Mandates**, settled in USDC on **HashKey Chain**, and released only after a verifier confirms the post is real.

## The problem

**Influencers** get offers over DMs ("post a story, get $3"). After posting, advertisers often disappear, refuse to pay, or change the terms, and nothing guarantees payment.

**Advertisers** who pay up front get scammed: no post, fake screenshots, or copied URLs. Contacting and negotiating with hundreds of influencers by hand doesn't scale.

**Root causes:** no mutual trust, no escrow, no way to check that content is authentic, and manual, opaque campaign handling.

## How ZyroXQ solves it

| Problem | Solution |
|---|---|
| Advertiser disappears or refuses to pay | Funds are locked through HSP **before** posting. They auto-release if the advertiser doesn't dispute in time |
| Advertiser changes the conditions | Deal terms are signed by both agents, and their hash is anchored to the payment |
| Influencer never posts | Nothing is released without proof. The advertiser is refunded after the deadline |
| Fake screenshots or copied URLs | Proof is a YouTube API lookup, matched to the influencer's OAuth-verified channel |
| Post deleted after payment | The fixed fee is released only after the post has stayed live for 48h |
| Manual outreach to hundreds | Agent discovery plus parallel A2A negotiation |
| No transparency | Every step is logged: terms, mandates, proofs and tx hashes, with a full audit trail |

**Core security rule:** *the LLM proposes, deterministic code decides, the key signs.* No payment path depends on LLM output alone.

---

## Project status

| Piece | Status |
|---|---|
| HSP merchant client (HMAC, ES256K Cart Mandate JWT, orders, payments, webhooks) | ✅ Built, unit-tested; not yet run against the live QA gateway |
| Policy engine (offers, signed terms, agent-key delegation, cart gate, budget) | ✅ Built and tested |
| Payment Mandate (x402 `exact` / EIP-3009 signature) + simulated HSP gateway | ✅ Built and tested |
| Influencer + Advertiser agents (LangGraph, A2A, parallel negotiation, wallet-signature pause) | ✅ Built and tested end to end |
| Verifier (YouTube checks, prompt-injection flag, signed attestations) | ✅ Built; uses the YouTube Data API key |
| CampaignEscrow.sol + Python escrow (simulated ledger / on-chain) | ✅ Compiles; simulated ledger tested; not yet deployed |
| MCP server (read-only tools) + LangChain assistant | ✅ Tools tested over streamable HTTP; assistant needs `ANTHROPIC_API_KEY` |
| PostgreSQL store + LangGraph Postgres checkpointer | ✅ Built; needs a database to test (`TEST_DATABASE_URL`) |
| Google sign-in + YouTube channel proof + MetaMask wallet linking | ✅ Built; OAuth flow tested with mocked Google |
| React + TypeScript web app (login → onboarding → advertiser / influencer workspaces, live SSE updates) | ✅ Built; typechecked |
| HSP merchant onboarding (`app_key` / `app_secret`) | 🚧 Pending |

### Run it

```bash
uv sync
cd web && npm install && npm run build && cd ..   # builds the React app into src/zyroxq/api/static
uv run zyroxq serve                              # http://localhost:8000
```

Frontend development with hot reload: run `uv run zyroxq serve`, then `cd web && npm run dev`, open
http://localhost:5173 and set `FRONTEND_URL=http://localhost:5173` in `.env`.

The app has no dummy data: people sign in with Google. **Advertisers** link a MetaMask wallet and launch campaigns.
**Influencers** grant read-only YouTube access, which proves their channel; link a payout wallet, which also authorizes
their agent key; and set their rates. Everything updates live over Server-Sent Events.

| Set in `.env` | Needed for |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` | Sign-in (redirect URI `{PUBLIC_BASE_URL}/auth/google/callback`) |
| `SESSION_SECRET` | Sessions and encrypted influencer agent keys that survive restarts |
| `YOUTUBE_API_KEY` | Verifying posts (YouTube Data API v3) |
| `DATABASE_URL` | Keeping users, deals and LangGraph checkpoints across restarts |
| `ANTHROPIC_API_KEY` | Claude negotiation, brief drafting and the campaign assistant (rules otherwise) |
| `VERIFIER_PRIVATE_KEY` | A stable attestation key (random per run otherwise) |
| `ESCROW_ADDRESS` + `OPERATOR_PRIVATE_KEY` | On-chain CampaignEscrow (`uv run python scripts/deploy_escrow.py`) |
| `SETTLEMENT_MODE=hosted` + HSP credentials | Real HSP orders; the advertiser pays in HSP's hosted checkout |

Until HSP credentials and the escrow are set, the gateway and escrow are emulated in-process with the same checks,
and the advertiser's **browser wallet** still signs the real EIP-712 Payment Mandate. The top bar shows what is
simulated. With the simulated escrow, **+49 h / +7 days** move the clock to trigger releases.

MCP for Claude Desktop (server must be running): `uv run zyroxq mcp` over stdio with `ZYROXQ_API_TOKEN` set to
`MCP_API_TOKEN`, or connect to `http://localhost:8000/mcp/` with that bearer token.

---

## System components

```
┌───────────────────────── ADVERTISER SIDE ─────────────────────────┐
│  Advertiser (human) ──brief/approvals──▶ ADVERTISER AGENT         │
│                                          (LangGraph, Payment Agent)│
│                                          + Policy Engine (code)    │
│                                          + Wallet Signer (code)    │
└──────────────┬────────────────────────────────────────────────────┘
               │ A2A (JSON-RPC over HTTP, Agent Cards)
┌──────────────▼──────────── INFLUENCER SIDE ───────────────────────┐
│  INFLUENCER AGENT ×N (LangGraph, Receiving Agent)                 │
│  rate card · floor price · OAuth-verified channel                 │
│  Influencer (human) ◀──notifications / submits video URL──        │
└──────────────┬────────────────────────────────────────────────────┘
               │
┌──────────────▼──────────── ZYROXQ PLATFORM (FastAPI) ─────────────┐
│  Deal Service ── creates Cart Mandates (the onboarded HSP merchant)│
│  HSP Webhook endpoint ── payment status → deal state              │
│  Verifier ── YouTube API checks → signed attestation              │
│  Scheduler ── deadlines, T+48h / T+7d checks, refunds             │
│  DB (PostgreSQL) ── deals, mandates, proofs, audit log            │
└──────┬──────────────────────────────┬─────────────────────────────┘
       │ HMAC + ES256K JWT            │ web3
┌──────▼──────────┐          ┌────────▼─────────────┐
│ HSP Gateway     │─execute─▶│ HashKey Chain Testnet │
│ (HashKey)       │          │ USDC · CampaignEscrow │
└─────────────────┘          └───────────────────────┘
```

### Tech stack

| Library | Its job in ZyroXQ |
|---|---|
| **LangGraph** | Each agent's workflow as a state machine: nodes, branches, parallel fan-out, human-approval pauses |
| **LangChain** | LLM calls inside nodes (Claude), structured output, tool binding |
| **MCP** + `langchain-mcp-adapters` | Read-only tools the agents can call (YouTube data, deal lookup, payment status) |
| **a2a-sdk** | Agent Cards, task and message exchange between advertiser and influencer agents |
| **LangSmith** | Traces every agent run, showing why the agent did each thing |
| **httpx + cryptography** | HSP client: HMAC request signing, ES256K merchant JWT |
| **eth-account** | EIP-712 Payment Mandate signature |
| **web3** | Escrow contract: fund status, release, refund |
| **FastAPI + uvicorn** | Platform API, HSP webhook receiver, A2A endpoints |
| **PostgreSQL** (psycopg 3) | Deals, mandates, proofs, audit log, and LangGraph checkpoints |

---

## End-to-end workflow

### Phase 0: Setup (once)
```
1. Generate the merchant secp256k1 key → send the public key to HashKey → receive app_key/app_secret
2. Deploy CampaignEscrow on HashKey Chain Testnet (chain 133)
3. Influencers onboard: YouTube OAuth → channel_id verified → Influencer Agent + Agent Card published
4. Advertiser funds a wallet with testnet USDC (hosted mode only; no faucet is documented yet, see Open questions)
```

### Phase 1: Campaign brief
```
Advertiser: "Fitness brand, $300 budget, 3 creators, 10k–100k subs, YouTube Shorts, max $40 CPM"
   │
   ▼
Advertiser Agent [plan_campaign]  → LLM turns the text into a structured CampaignSpec
                                    → Policy Engine validates the budget against the wallet limits
```
The **Content requirements** card on *New campaign* sets the format (YouTube Short ≤ 3 min or a regular video), the min/max
video length and a requirements document of up to 15,000 characters (about 1–5 pages). You can type it, import a .txt/.md
file or start from an outline. The document goes to every influencer agent with the offer. Its keccak hash
(`requirements_hash`) and the length bounds are part of the signed `DealTerms`, and the policy engine refuses terms that
changed them. At proof time the verifier reads the real duration from YouTube `contentDetails` and fails the
`video_length` check when it is out of range.

### Phase 2: Discovery (A2A)
```
[discover] → fetch Influencer Agent Cards from the registry
           → filter by niche, verified subscribers, rate card  (code, not LLM)
           → shortlist N influencers
```

### Phase 3: Parallel negotiation (A2A + LangGraph fan-out)
```
For each influencer (in parallel, LangGraph Send API):
  Advertiser Agent ──A2A message: Offer{fixed $15, $2/1K views, cap $40}──▶ Influencer Agent
  Influencer Agent: LLM may suggest → Agent Policy Engine decides (hard limits, scoring) → Counter{fixed $20}
  Advertiser Agent: LLM may suggest → Agent Policy Engine + check_offer decide → Accept
  Influencer Agent ──A2A artifact: signed DealTerms──▶ Advertiser Agent
Bounded by the campaign's max rounds (default 4). Decline, no movement and escalation are valid outcomes.
```
See [Configurable agents](#configurable-agents-objectives-constraints-preferences) for how each side decides.

### Phase 4: Cart Mandate (merchant side, HSP)
```
Advertiser Agent → ZyroXQ Deal Service: "create order for these accepted DealTerms"
Deal Service:
  verify the influencer's signature on DealTerms
  build Cart Mandate { id, items: fixed + max performance = cap, pay_to: Escrow, chain 133, USDC }
  sign merchant_authorization (ES256K JWT, cart_hash)
  POST /api/v1/merchant/orders (HMAC) → payment_url, payment_request_id
```

### Phase 5: Payment Mandate (payer side), the core of the track
```
Advertiser Agent [policy_gate]  ← deterministic checks:
    ✔ Cart amount == agreed cap      ✔ pay_to == our escrow
    ✔ chain/token allowed            ✔ total spend ≤ campaign budget
    ✔ no duplicate deal_id           ✔ cart not expired
    ✔ atomic budget reservation (row lock), so concurrent deals cannot overspend
   │
   ├─ [approval] LangGraph interrupt → server drafts the EIP-712 data → advertiser signs in MetaMask
   ▼
[verify + submit]       server re-checks amount, recipient, token, chain, payer, expiry, budget and the
                        signature, claims the nonce once, records a payment attempt, then submits
[submit_to_hsp]         HSP verifies → executes transferWithAuthorization on the token
```

### Phase 6: Settlement confirmation
```
HSP → POST /webhooks/hsp (X-Signature verified; every transition idempotent)
   payment-included  → deal = FUNDING
   payment-finalized → a claim, not proof: the settlement receipt must show Transfer(payer → escrow, exact cap)
                       of the configured token with enough confirmations; the tx is bound to one deal only
                       → escrow.markFunded(deal, tx) → deal = FUNDED ✅ → A2A "money locked, tx 0x…"
   payment-failed    → deal = PAYMENT_FAILED, budget released
   timeout / 5xx     → deal = PAYMENT_UNKNOWN → reconciled (HSP lookup + chain), never re-signed
```
See **Funding** below for the details and what is still blocked.

### Phase 7: Content and verification
```
Influencer posts the Short → gives the URL to their agent → A2A: ProofSubmitted{video_id}
Verifier (code):
  1. video exists                     (YouTube API)
  2. owned by the deal's channel_id   ← blocks copied URLs
  3. published within the deadline
  4. description has #ad + @brand
  5. (optional) LLM vision check for the logo → low confidence = manual review, never auto-pay
  → signed Attestation{deal_id, video_id, evidence_hash}
```

### Phase 8: Payouts
```
T+48h: still live? → release FIXED fee to the influencer          (escrow.release)
T+7d:  views = YouTube API → perf = min(cap - fixed, views/1000 × rate) → release
       remainder → refund to the advertiser
No proof by the deadline → full refund
Advertiser disputes in the review window → DISPUTED → arbiter decides the split
```

---

## Deal state machine

Enforced by `models.DEAL_TRANSITIONS`: `DealService.save` refuses any other move.

```
NEGOTIATING → AGREED → CART_CREATED → AWAITING_APPROVAL → SIGNED → FUNDING → FUNDED
 ├→ REJECTED            └→ BLOCKED      │  (needs the        (payment   (settling)  (settlement
 └→ ESCALATED (a policy handed it to a human; nothing agreed or reserved)
                                        │   signature)        pending)               verified)
                                        ├→ PAYMENT_EXPIRED   SIGNED / FUNDING / AWAITING_CHECKOUT
                                        ├→ PAYMENT_FAILED      ⇄ PAYMENT_UNKNOWN (timeout or unverifiable
                                        ├→ REJECTED (declined)    evidence: reconciled, never re-signed)
                                        └→ AWAITING_CHECKOUT (hosted)   any of them → PAYMENT_FAILED | FUNDED

FUNDED → VERIFIED → FIXED_RELEASED → COMPLETED (performance paid, remainder refunded)
  │         │  └→ DISPUTED (payouts frozen)
  │         └→ PROOF_REJECTED (resubmit before the deadline)
  └→ EXPIRED_REFUNDED (no valid proof by the deadline)
```
Creators are told to post only at FUNDED, and proofs are refused before it.

---

## Agent design (LangGraph)

**Advertiser Agent graph**
```
plan_campaign → discover → ┬─ negotiate_deal (subgraph, ×N in parallel) ─┐
                           │   offer → wait_reply → llm_evaluate →        │
                           │   policy_check → {counter | accept | reject} │
                           └──────────────────────────────────────────────┘
                → collect_accepted → request_cart → policy_gate
                → [approval: wallet signs in browser] → verify_signature → submit_hsp
                → await_funding → monitor_deals → END
```

**Influencer Agent graph**
```
receive_offer → llm_evaluate → floor_price_rule → {counter | accept | reject}
              → sign_terms → notify_human → await_funding → collect_proof → track_payouts
```

A LangGraph PostgreSQL checkpointer (`langgraph-checkpoint-postgres`) lets each deal survive restarts and wait days for proof.

## Configurable agents: objectives, constraints, preferences

Each creator and each advertiser configures their agent in the **Agent Profile Builder** (`/influencer/agent`,
`/advertiser/agent`). A profile has three layers and is saved as an immutable version (`versioned_docs`):

| Layer | Creator agent | Advertiser agent |
|---|---|---|
| Objective | primary goal (earnings, guaranteed income, brand fit, partnership, workload) + weights | primary goal (ROI, cost, reach, relevance, evidence quality, speed) + weights |
| Hard constraints | minimum fixed fee and CPM, accepted compensation models, shortest publish window, max revisions, paid amplification, license and exclusivity limits, forbidden categories, brief required | per-campaign ceilings (max fixed, max CPM, cap, budget) + minimum verified subscribers, verified channel, blocked niches |
| Preferences | opening ask, expected views (own estimate), preferred categories, brand-fit discount, acceptance score, escalate instead of decline, premiums for paid amplification / exclusivity / license / rush / extra revisions | opening percentages, concession per round, niche-fit premium, escalate above a fixed fee, acceptance score |

**Decision order** (`agent_policy/engine.py`, no LLM, no I/O): hard constraints → money limits (with premiums) →
utility score → `ACCEPT` / `COUNTEROFFER` / `REJECT` / `REQUEST_CLARIFICATION` / `ESCALATE_TO_HUMAN`. An LLM
suggestion is used only where it still passes every check; overrides are recorded. Every decision is stored as
a record with the objective, each hard check (pass/fail), the preferences applied, the scores, the action, the
policy and pricing versions and the offer ids (`GET /api/deals/{id}/decisions`, own party only).

**Privacy.** Profiles never leave their owner: the creator directory strips the rate card, A2A replies carry
only fixed-template explanations (`public_explanation`), the model's note and the private rationale stay in
the owner's decision record, offers carry template notes, and the creator's deal timeline hides the
advertiser's moves and policy-gate details. Tests assert no floor value reaches the advertiser's API views.

**Pricing** (`agent_policy/pricing.py`, version signed into the terms): `fixed` pays the fee (cap = fee),
`cpm` pays `min(cap, views/1000 × CPM)`, `hybrid` pays `min(cap, fixed + views/1000 × CPM)`, rounded down to
the cent. Example: $19 + $2.50 × 10,000/1,000 = $44 → capped at $40. Eligible views are YouTube's count when the
performance fee is due, for a post that is still public and passes its checks.

**Evidence** (`agent_policy/evidence.py`): each metric has a source (`youtube_data_api`, `self_reported`,
`derived_estimate`, `unavailable`), a timestamp and a verified flag. Subscribers and channel come from the
YouTube Data API; audience countries are self-reported; demographics and engagement are unknown because
ZyroXQ has no YouTube Analytics access. Unknown is never filled in.

**Sponsorship disclosure** (`compliance/`): versioned policies for India (Department of Consumer Affairs
"Endorsements Know-hows!", ASCI) and the US (FTC Endorsement Guides, "Disclosures 101"), seeded as version 1
and updatable by the operator (`PUT /api/disclosure-policies/{jurisdiction}`, operator token). A campaign
snapshots the versions (`US@1`) and they are signed into the terms. Gates: campaign creation fails without a
policy and an approved required tag per jurisdiction; creator agents decline offers without a compliant
label; proofs need an approved label in the title or the first N characters of the description and the
creator's attestation that the disclosure is in the video (recorded as a statement, not machine-verified);
optionally the advertiser reviews each post before the fixed fee is released. These are product rules that
summarize public guidance: **not legal advice, no guarantee of compliance.**

**Agreement and approval.** The signed DealTerms are the canonical agreement and `terms_hash()` its id. The
payment authorization is drafted for that hash (`approved_terms_hash`), approvals record it, and
`authorization_problems` refuses to submit if the terms changed afterwards. Payment then follows the
[funding flow](#funding-how-money-reaches-the-escrow) unchanged.

| API | |
|---|---|
| `GET/PUT /api/me/agent-policy`, `POST …/validate`, `GET …/versions` | profile (loosening a hard constraint needs `confirm_material_change`, else 409 with the list) |
| `GET /api/me/evidence`, `GET /api/influencers/{id}/evidence` | sourced creator metrics + public profile |
| `GET /api/deals/{id}/negotiation`, `GET /api/deals/{id}/decisions` | Negotiation Room, own decision records |
| `GET /api/campaigns/{id}/compliance`, `POST /api/deals/{id}/compliance/review` | Compliance & Audit, advertiser review |
| `GET /api/disclosure-policies`, `PUT /api/disclosure-policies/{jurisdiction}` | policies (operator publishes) |
| `GET /api/pricing/rules` | formulas, version, worked example |

## MCP server (what the LLM is allowed to touch)

One read-only `zyroxq` MCP server, embedded at `/mcp/` (streamable HTTP) and runnable over stdio (`uv run zyroxq mcp`):

| Tool | Returns |
|---|---|
| `list_campaigns` | Campaigns with status, budget, niche |
| `list_deals` | Deals of a campaign: state, agreed amounts, payouts |
| `get_deal` | Negotiation rounds, signed terms, HSP status, proof checks, attestations |
| `get_deal_timeline` | Audit log of one deal |
| `campaign_spend` | Locked via HSP, paid to creators, refunded, still in escrow |
| `get_video_stats` | A YouTube video as the verifier sees it |

Signing, creating orders and releasing funds are deliberately **not** tools. They are fixed graph nodes that run only after the policy engine passes.

---

## Funding: how money reaches the escrow

**The budget is a spending limit, not a balance.** Creating a $120 campaign moves nothing. When a deal passes the policy
gate, its cap is *reserved* from the budget in one atomic step (`store.reserve_budget`: a `SELECT … FOR UPDATE` on the
campaign row in PostgreSQL), so concurrent deals cannot overspend. A reservation is released when the deal is declined,
blocked, fails or expires unused.

**The signature authorizes; it does not pay.** For each deal the server drafts an EIP-3009 `TransferWithAuthorization`
(EIP-712, domain = the token contract on chain 133): `from` = the campaign's linked wallet, `to` = the escrow,
`value` = the cap in token units (6 decimals), a validity window (`AUTHORIZATION_TTL_SECONDS`) and a random 32-byte nonce.
MetaMask signs it with `eth_signTypedData_v4`. The browser cannot change it, and the server re-checks every field, the
expiry, the budget and the signer before submitting. Each nonce is claimed once for one deal (`store.claim`), and a
payment attempt (idempotency key = nonce) is persisted before submission. The signing panel shows the budget, reserved
and remaining amounts, the deal amounts, the escrow address, the expiry, the connected wallet and network, and the
wallet's USDC balance with an insufficient-balance warning. The balance is advisory, because it can change before
settlement. Deals settle independently: with funds for two of three deals, two are funded and the third fails with
nothing locked.

**FUNDED requires evidence.** A webhook, an HTTP 200 or a lookup only claims success. `chain/settlement.py` reads the
settlement transaction's receipt and requires all of these:
- status 1;
- the configured chain;
- an ERC-20 `Transfer` emitted by the configured token, from the payer to the escrow, for exactly the cap;
- at least `SETTLEMENT_MIN_CONFIRMATIONS` confirmations.

The transaction hash is then claimed for that one deal and bound in the contract with `markFunded(dealId, fundingTx)`.
The contract refuses a reused hash and still checks that the unallocated balance covers the cap.

Outcomes:
- No receipt yet: the deal stays FUNDING.
- Contradicting evidence, a reused transaction or a timeout: the deal becomes PAYMENT_UNKNOWN. The scheduler
  reconciles it through `GET /merchant/payments` and the chain. A deal is marked failed only when HSP says so, or when
  the authorization expired and the token reports it unused (`authorizationState`). A payment is never re-signed
  blindly.

**CampaignEscrow guarantees:**
- Fixed at deployment: the token, the operator, the verifier and the arbiter.
- Funding is attributed per deal by its settlement transaction.
- Payouts need verifier attestations, which are replay-protected and capped per deal.
- Refunds go only to the deal's advertiser.
- `nonReentrant` on every function that transfers tokens.

`SimulatedEscrow` mirrors these rules. Change both together.

| | Simulated (`SETTLEMENT_MODE=simulated`, default) | Hosted (`SETTLEMENT_MODE=hosted`) |
|---|---|---|
| Payment | Browser-signed authorization, verified by an in-process HSP emulator | HSP hosted checkout (`payment_url`): the payer-side API is undocumented |
| Settlement evidence | The simulated escrow's own receipts (tx hashes start `0x5111`) | Transaction receipt on HashKey Chain Testnet |
| Money | **None moves.** Deals, payouts and refunds are labelled "simulated" | Real testnet USDC |
| Start-up | Refuses `ESCROW_ADDRESS` | Refuses to start unless `HSP_APP_KEY`, `HSP_APP_SECRET`, the merchant key file, `ESCROW_ADDRESS`, `OPERATOR_PRIVATE_KEY` and `VERIFIER_PRIVATE_KEY` are set, the RPC is chain 133 and the deployed escrow's `token()`, `operator()` and `verifier()` match. It never falls back to simulation |

**Configure hosted mode:**
1. `uv run zyroxq gen-merchant-key`, then register the public key with HashKey to receive `HSP_APP_KEY` / `HSP_APP_SECRET`.
2. `uv run python scripts/compile_contracts.py`.
3. Run `uv run python scripts/deploy_escrow.py` with `OPERATOR_PRIVATE_KEY` (holding testnet HSK for gas) and
   `VERIFIER_PRIVATE_KEY`. This sends a real transaction, and it prints `ESCROW_ADDRESS`.
4. Set `SETTLEMENT_MODE=hosted` and restart. `/api/status` shows the mode.

The advertiser needs testnet USDC in the linked wallet. No faucet is documented, so ask HashKey (Open questions above).

**Still blocked externally:**
- The HSP payer-side submission.
- HSP's support for `pay_to` being a contract.
- The QA Cloudflare 403.
- A USDC faucet.
- Confirming the testnet USDC's EIP-3009 domain and `authorizationState`.
- The `GET /merchant/payments` response shape.

The contract has not been deployed.

**Tests:**
- `uv run pytest` runs everything without credentials or funds. `tests/test_funding.py` covers each funding failure
  mode, and `tests/test_e2e.py` covers the API flow.
- Set `TEST_DATABASE_URL` to also run the PostgreSQL atomicity tests. Use a throwaway database.
- The contract is compiled with solc 0.8.24, but there is no local-EVM test. `eth-tester[py-evm]` does not build on
  Windows / Python 3.13 (`safe-pysha3`), so `SimulatedEscrow` tests stand in for the contract rules.

## Folder structure

```
src/zyroxq/
  __init__.py          CLI: zyroxq serve | mcp | gen-merchant-key
  config.py            pydantic-settings (.env)
  models.py            CampaignSpec, Offer, DealTerms, Deal, DealState, Attestation, AuditEvent
  clock.py             wall clock with dev fast-forward (simulated escrow only)
  hsp/                 canonical · auth (HMAC, ES256K JWT) · cart · client · webhook · networks
  policy/engine.py     every payment rule, pure functions
  agent_policy/        configurable agents: profiles (3 layers), decision engine, pricing, evidence, material changes
  compliance/          versioned sponsorship-disclosure policies (IN, US) + deterministic checks
  wallet/              signer (EIP-3009 Payment Mandate, EIP-191 terms) · gateway (simulated / hosted HSP)
  chain/escrow.py      SimulatedEscrow + OnchainEscrow (same rules as the contract)
  chain/settlement.py  settlement evidence: receipt → Transfer(payer → escrow, exact amount); simulated twin
  chain/balances.py    advisory USDC balance reads for the signing panel
  verifier/            youtube (Data API; fixture source for tests) · verify (checks) · attest (signed releases)
  auth/                Google OAuth + YouTube channel proof · wallet-link messages · agent-key encryption · API tokens
  agents/influencer/   LangGraph negotiation graph + A2A server + hub (one agent per onboarded creator)
  agents/advertiser/   LangGraph campaign graph · negotiation moves · runner · MCP assistant
  agents/a2a_client.py A2A client connector
  services/            context (wires backends) · deals (lifecycle, webhooks, payouts) · registry · events (SSE bus)
  store/               memory + PostgreSQL (JSONB) stores
  mcp_servers/         read-only FastMCP server
  api/                 FastAPI app (sessions, role-scoped REST, SSE, SPA host)
web/                   React + TypeScript + Vite app (login → onboarding → advertiser / influencer workspaces)
contracts/CampaignEscrow.sol (+ build/ ABI)
scripts/               compile_contracts · deploy_escrow · smoke_hsp_order
tests/
```

## Build roadmap

| # | Milestone | Proves |
|---|---|---|
| 1 | HSP client + smoke order on QA | Gateway access works (the biggest risk) |
| 2 | Models + policy engine + tests | Core security logic |
| 3 | Influencer and Advertiser agents negotiating over A2A (mock payment) | The A2A story |
| 4 | Cart Mandate → Payment Mandate → HSP → webhook | The track requirement end to end |
| 5 | Verifier (YouTube) + attestation | Proof-of-post |
| 6 | Escrow contract + release/refund | Conditional payouts |
| 7 | MCP servers + LangSmith traces | Polish and observability |
| 8 | Web app + attack tests + demo video | The pitch |

## Open questions for the HashKey team

| Question | If yes | If no (fallback) |
|---|---|---|
| Can an agent sign and submit the Payment Mandate programmatically? | Fully automated Phase 5 | The agent prepares everything, and the advertiser signs in HSP's hosted checkout (`payment_url`), as human-in-the-loop signing |
| Can `pay_to` be a smart-contract address? | Funds lock in escrow up front | Pay-on-proof: a Cart Mandate is created after verification with `pay_to` = influencer |
| Can the Cloudflare 403 on the QA gateway be lifted? | Direct API calls | IP allowlist, or run from a cloud VM |
| What is the payer-side endpoint and payload (`/api/v1/payment/pay-mandate`)? | Browser-signed EIP-3009 authorizations are submitted directly in hosted mode | Hosted checkout (`payment_url`); `HostedGateway.submit_payment_mandate` stays disabled |
| What does `GET /merchant/payments` return (field names for status and tx hash)? | Reconciliation reads it exactly | The adapter reads the webhook field names (`status`, `tx_signature`) and treats any other shape as "no record" |
| Does HashKey testnet USDC (`0x8FE3…53c6`) implement EIP-3009 `authorizationState` and the domain `USD Coin` / `2`? | Signatures verify on chain; expired-unused authorizations can be proven | Check the token contract before going live; the EIP-712 domain is set in `wallet/signer.py` |
| Is there a testnet USDC faucet on HashKey Chain Testnet? | Advertisers fund their wallet themselves | Hosted-mode demos need USDC from HashKey |

---

## Getting started

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # or: pip install -r requirements.txt
cp .env.example .env         # fill in Google OAuth, SESSION_SECRET, YOUTUBE_API_KEY, ...
cd web && npm install && npm run build && cd ..
uv run zyroxq serve
```

Development:

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
cd web && npm run typecheck
```

`pyproject.toml` and `uv.lock` are the source of truth. After `uv add`, regenerate the pip files:

```bash
uv export --format requirements-txt --no-hashes --no-dev --no-emit-project -o requirements.txt
uv export --format requirements-txt --no-hashes --only-dev --no-emit-project -o requirements-dev.txt
```

## References

- HSP (HashKey Merchant) API docs: https://hashkey-merchant.github.io/#/
- AP2 (Agent Payments Protocol) spec: https://ap2-protocol.org/specification/
- A2A protocol: https://a2a-protocol.org/
