"""ZyroXQ MCP server — read-only tools over the ZyroXQ REST API.

Deliberately read-only: creating orders, signing and releasing funds are graph nodes behind the
policy engine, never tools an LLM can call.

Run standalone (stdio, e.g. for Claude Desktop):  uv run zyroxq mcp   (ZYROXQ_API_TOKEN = MCP_API_TOKEN)
Embedded in the API server at /mcp (streamable HTTP) for the advertiser assistant. Each call
forwards the caller's bearer token to the REST API, so tools only see that user's campaigns.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP


def _caller_auth(ctx: Context | None) -> str | None:
    try:
        request = ctx.request_context.request if ctx is not None else None
    except ValueError:  # called outside an MCP request (stdio startup, direct calls)
        request = None
    return request.headers.get("authorization") if request is not None else None


def build_mcp(api_base: str, http: httpx.AsyncClient | None = None, streamable_http_path: str = "/mcp", default_token: str = "") -> FastMCP:
    client = http or httpx.AsyncClient(base_url=api_base.rstrip("/"), timeout=20)
    mcp = FastMCP(
        "zyroxq",
        instructions="Read-only access to ZyroXQ influencer campaigns: deals, negotiation history, payments, proofs and payouts.",
        streamable_http_path=streamable_http_path,
        stateless_http=True,
    )

    async def get(path: str, ctx: Context | None) -> Any:
        auth = _caller_auth(ctx) or (f"Bearer {default_token}" if default_token else None)
        res = await client.get(path, headers={"Authorization": auth} if auth else None)
        res.raise_for_status()
        return res.json()

    @mcp.tool()
    async def list_campaigns(ctx: Context) -> list[dict[str, Any]]:
        """List campaigns with status, budget and niche."""
        return [
            {"id": c["spec"]["id"], "advertiser": c["spec"]["advertiser"], "status": c["status"], "budget": c["spec"]["budget"], "niche": c["spec"]["niche"]}
            for c in await get("/api/campaigns", ctx)
        ]

    @mcp.tool()
    async def list_deals(campaign_id: str, ctx: Context) -> list[dict[str, Any]]:
        """Deals of a campaign: influencer, state, agreed amounts and payouts."""
        return [
            {
                "deal_id": d["id"],
                "influencer": d["influencer_id"],
                "state": d["state"],
                "fixed_fee": d["terms"]["fixed_fee"] if d.get("terms") else None,
                "cpm": d["terms"]["cpm"] if d.get("terms") else None,
                "cap": d["terms"]["cap"] if d.get("terms") else None,
                "paid_out": [{"kind": p["kind"], "amount": p["amount"]} for p in d.get("payouts", [])],
            }
            for d in await get(f"/api/campaigns/{campaign_id}/deals", ctx)
        ]

    @mcp.tool()
    async def get_deal(deal_id: str, ctx: Context) -> dict[str, Any]:
        """Full deal record: negotiation rounds, signed terms, HSP payment status, proof checks, attestations, payouts."""
        return await get(f"/api/deals/{deal_id}", ctx)

    @mcp.tool()
    async def get_deal_timeline(deal_id: str, ctx: Context) -> list[dict[str, Any]]:
        """Audit log of one deal in order: who did what (agents, policy, HSP, verifier, escrow)."""
        return await get(f"/api/deals/{deal_id}/events", ctx)

    @mcp.tool()
    async def campaign_spend(campaign_id: str, ctx: Context) -> dict[str, str]:
        """Money locked in escrow, paid to creators, and refunded for a campaign (USD)."""
        deals = await get(f"/api/campaigns/{campaign_id}/deals", ctx)
        locked = sum((Decimal(d["terms"]["cap"]) for d in deals if d.get("funding_tx") and d.get("terms")), Decimal(0))
        paid = sum((Decimal(p["amount"]) for d in deals for p in d.get("payouts", []) if p["kind"] != "refund"), Decimal(0))
        refunded = sum((Decimal(p["amount"]) for d in deals for p in d.get("payouts", []) if p["kind"] == "refund"), Decimal(0))
        return {"locked_total": str(locked), "paid_to_creators": str(paid), "refunded": str(refunded), "in_escrow": str(locked - paid - refunded)}

    @mcp.tool()
    async def get_video_stats(video: str, ctx: Context) -> dict[str, Any]:
        """Look up a YouTube video (URL or id) as the verifier sees it: channel, publish time, views, privacy."""
        return await get(f"/api/videos/{video}", ctx)

    return mcp


def main() -> None:
    build_mcp(os.environ.get("ZYROXQ_API_URL", "http://localhost:8000"), default_token=os.environ.get("ZYROXQ_API_TOKEN", "")).run()


if __name__ == "__main__":
    main()
