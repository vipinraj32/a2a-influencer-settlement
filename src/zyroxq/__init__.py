"""ZyroXQ — agent-negotiated influencer deals settled through HashKey HSP."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _serve(host: str, port: int) -> None:
    import uvicorn

    from .api.app import create_app

    config = uvicorn.Config(create_app(), host=host, port=port, log_level="info")
    # psycopg's async driver cannot run on Windows' default Proactor loop
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=loop_factory)


def _gen_merchant_key(out_dir: str) -> None:
    from .hsp.auth import generate_merchant_key

    out = Path(out_dir)
    private_path, public_path = out / "merchant_private_key.pem", out / "merchant_public_key.pem"
    if private_path.exists():
        sys.exit(f"{private_path} already exists — refusing to overwrite")
    out.mkdir(parents=True, exist_ok=True)
    private, public = generate_merchant_key()
    private_path.write_bytes(private)
    public_path.write_bytes(public)
    print(f"Wrote {private_path} (keep secret) and {public_path} (send to HashKey for onboarding)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="zyroxq")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the platform server (API, dashboard, A2A agents, MCP)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("mcp", help="run the read-only MCP server over stdio (set ZYROXQ_API_URL)")
    key = sub.add_parser("gen-merchant-key", help="generate the secp256k1 key pair for HSP onboarding")
    key.add_argument("--out", default="keys")
    args = parser.parse_args()

    # pydantic-settings reads .env into Settings only; LangSmith reads LANGSMITH_* from the process environment
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "serve":
        _serve(args.host, args.port)
    elif args.cmd == "mcp":
        from .mcp_servers.server import main as mcp_main

        mcp_main()
    elif args.cmd == "gen-merchant-key":
        _gen_merchant_key(args.out)
