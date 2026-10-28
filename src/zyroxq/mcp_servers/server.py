"""ZyroXQ MCP server — read-only tools over the ZyroXQ REST API.

Deliberately read-only: creating orders, signing and releasing funds are graph nodes behind the
policy engine, never tools an LLM can call.

Run standalone (stdio, e.g. for Claude Desktop):  uv run zyroxq mcp
Embedded in the API server at /mcp (streamable HTTP) for the advertiser assistant.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


def build_mcp(api_base: str, http: httpx.AsyncClient | None = None, streamable_http_path: str = "/mcp") -> FastMCP:
    client = http or httpx.AsyncClient(base_url=api_base.rstrip("/"), timeout=20)
    mcp = FastMCP(
        "zyroxq",
        instructions="Read-only access to ZyroXQ influencer campaigns: deals, negotiation history, payments, proofs and payouts.",
        streamable_http_path=streamable_http_path,
        stateless_http=True,
    )

    async def get(path: str) -> Any:
        res = await client.get(path)
        res.raise_for_status()
        return res.json()

    @mcp.tool()
    async def list_campaigns() -> list[dict[str, Any]]:
        """List campaigns with status, budget and niche."""
        return [
            {"id": c["spec"]["id"], "advertiser": c["spec"]["advertiser"], "status": c["status"], "budget": c["spec"]["budget"], "niche": c["spec"]["niche"]}
            for c in await get("/api/campaigns")
        ]

    @mcp.tool()
    async def list_deals(campaign_id: str) -> list[dict[str, Any]]:
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
            for d in await get(f"/api/campaigns/{campaign_id}/deals")
        ]

    @mcp.tool()
    async def get_deal(deal_id: str) -> dict[str, Any]:
        """Full deal record: negotiation rounds, signed terms, HSP payment status, proof checks, attestations, payouts."""
        return await get(f"/api/deals/{deal_id}")

    @mcp.tool()
    async def get_deal_timeline(deal_id: str) -> list[dict[str, Any]]:
        """Audit log of one deal in order: who did what (agents, policy, HSP, verifier, escrow)."""
        return await get(f"/api/deals/{deal_id}/events")

    @mcp.tool()
    async def campaign_spend(campaign_id: str) -> dict[str, str]:
        """Money locked in escrow, paid to creators, and refunded for a campaign (USD)."""
        deals = await get(f"/api/campaigns/{campaign_id}/deals")
        locked = sum((Decimal(d["terms"]["cap"]) for d in deals if d.get("funding_tx") and d.get("terms")), Decimal(0))
        paid = sum((Decimal(p["amount"]) for d in deals for p in d.get("payouts", []) if p["kind"] != "refund"), Decimal(0))
        refunded = sum((Decimal(p["amount"]) for d in deals for p in d.get("payouts", []) if p["kind"] == "refund"), Decimal(0))
        return {"locked_total": str(locked), "paid_to_creators": str(paid), "refunded": str(refunded), "in_escrow": str(locked - paid - refunded)}

    @mcp.tool()
    async def get_video_stats(video: str) -> dict[str, Any]:
        """Look up a YouTube video (URL or id) as the verifier sees it: channel, publish time, views, privacy."""
        return await get(f"/api/videos/{video}")

    return mcp


def main() -> None:
    build_mcp(os.environ.get("ZYROXQ_API_URL", "http://localhost:8000")).run()


if __name__ == "__main__":
    main()
