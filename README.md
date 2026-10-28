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
| Python 3.13 + uv project, package `zyroxq` | ✅ Done |
| Dependencies (LangChain, LangGraph, LangSmith, MCP, A2A, web3…) | ✅ Installed and import-tested |
| HSP client, agents, MCP servers, verifier, escrow, UI | 🚧 Not started |
| HSP merchant onboarding (`app_key` / `app_secret`) | 🚧 Pending |

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
│  DB (SQLite) ── deals, mandates, proofs, audit log                │
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

---

## End-to-end workflow

### Phase 0: Setup (once)
```
1. Generate the merchant secp256k1 key → send the public key to HashKey → receive app_key/app_secret
2. Deploy CampaignEscrow on HashKey Chain Testnet (chain 133)
3. Influencers onboard: YouTube OAuth → channel_id verified → Influencer Agent + Agent Card published
4. Advertiser funds a wallet with testnet USDC
```

### Phase 1: Campaign brief
```
Advertiser: "Fitness brand, $300 budget, 3 creators, 10k–100k subs, YouTube Shorts, max $40 CPM"
   │
   ▼
Advertiser Agent [plan_campaign]  → LLM turns the text into a structured CampaignSpec
                                    → Policy Engine validates the budget against the wallet limits
```

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
  Influencer Agent: LLM evaluates → rules enforce the floor price → Counter{fixed $20}
  Advertiser Agent: LLM proposes a reply → POLICY checks it (≤ max CPM, ≤ budget) → Accept
  Influencer Agent ──A2A artifact: signed DealTerms──▶ Advertiser Agent
Max rounds = 3, then walk away.
```

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
   │
   ├─ amount > approval threshold → [human_approval] LangGraph interrupt → advertiser clicks Approve
   ▼
[sign_payment_mandate]  wallet signs EIP-712 (code node, NOT an LLM tool)
[submit_to_hsp]         HSP Gateway verifies → broadcasts on-chain
```

### Phase 6: Settlement confirmation
```
HSP → POST /webhooks/hsp (X-Signature verified, idempotent on request_id)
   payment-included  → deal = FUNDING
   payment-finalized → deal = FUNDED ✅ → A2A message to Influencer Agent: "money locked, tx 0x…"
   payment-failed    → deal = FAILED → Advertiser Agent retries or cancels
```

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

```
NEGOTIATING → AGREED → CART_CREATED → AWAITING_APPROVAL → SIGNED → FUNDING → FUNDED
   │                                                                    │
   └→ REJECTED                              ┌──────────────────────────┘
                                            ▼
                         AWAITING_PROOF → PROOF_SUBMITTED → VERIFIED → FIXED_RELEASED → COMPLETED
                               │                │              │                          (perf paid,
                               ▼                ▼              ▼                           rest refunded)
                           EXPIRED →         REJECTED       DISPUTED → RESOLVED
                           REFUNDED         (resubmit)
```

---

## Agent design (LangGraph)

**Advertiser Agent graph**
```
plan_campaign → discover → ┬─ negotiate_deal (subgraph, ×N in parallel) ─┐
                           │   offer → wait_reply → llm_evaluate →        │
                           │   policy_check → {counter | accept | reject} │
                           └──────────────────────────────────────────────┘
                → collect_accepted → request_cart → policy_gate
                → [human_approval?] → sign_mandate → submit_hsp
                → await_funding → monitor_deals → END
```

**Influencer Agent graph**
```
receive_offer → llm_evaluate → floor_price_rule → {counter | accept | reject}
              → sign_terms → notify_human → await_funding → collect_proof → track_payouts
```

A LangGraph SQLite checkpointer lets each deal survive restarts and wait days for proof.

## MCP tool servers (what the LLM is allowed to touch)

| MCP server | Tools | Risk |
|---|---|---|
| `youtube-mcp` | `get_channel`, `get_video`, `get_video_stats` | Read-only |
| `deals-mcp` | `list_deals`, `get_deal`, `campaign_spend` | Read-only |
| `hsp-mcp` | `get_payment_status` | Read-only |
| `escrow-mcp` | `get_escrow_balance` | Read-only |

Signing, creating orders and releasing funds are deliberately **not** tools. They are fixed graph nodes that run only after the policy engine passes.

---

## Planned folder structure

```
src/zyroxq/
  config.py            pydantic-settings (.env)
  models.py            CampaignSpec, Offer, DealTerms, Attestation, DealState
  hsp/                 canonical.py · auth.py · cart.py · client.py · webhook.py
  policy/engine.py     all payment rules, pure functions, heavily tested
  wallet/signer.py     EIP-712 Payment Mandate signing
  chain/escrow.py      web3 wrapper
  agents/advertiser/   graph.py · nodes.py · a2a_executor.py
  agents/influencer/   graph.py · nodes.py · a2a_executor.py
  verifier/            youtube.py · attest.py
  mcp_servers/         youtube.py · deals.py · hsp.py · escrow.py
  api/                 FastAPI app: deals, webhooks, A2A mounts, dashboard API
  db.py                SQLite (deals + audit log)
contracts/CampaignEscrow.sol
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
| 8 | Dashboard + attack demos + demo video | The pitch |

## Open questions for the HashKey team

| Question | If yes | If no (fallback) |
|---|---|---|
| Can an agent sign and submit the Payment Mandate programmatically? | Fully automated Phase 5 | The agent prepares everything, and the advertiser signs in HSP's hosted checkout (`payment_url`), as human-in-the-loop signing |
| Can `pay_to` be a smart-contract address? | Funds lock in escrow up front | Pay-on-proof: a Cart Mandate is created after verification with `pay_to` = influencer |
| Can the Cloudflare 403 on the QA gateway be lifted? | Direct API calls | IP allowlist, or run from a cloud VM |

---

## Getting started

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # or: pip install -r requirements.txt
cp .env.example .env         # fill in API keys and HSP credentials
uv run zyroxq
```

Development:

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
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
