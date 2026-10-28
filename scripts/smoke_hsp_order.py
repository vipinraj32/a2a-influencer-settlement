"""Creates a $1 one-time order on the HSP QA gateway and prints the checkout URL — the first thing
to run once HashKey has issued app_key / app_secret.

Usage: uv run python scripts/smoke_hsp_order.py
"""

import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from zyroxq.hsp import HspApiError, HspClient, build_cart_contents, find_token

ROOT = Path(__file__).resolve().parent.parent


def env(name: str) -> str:
    return os.environ.get(name) or sys.exit(f"{name} is not set (see .env.example)")


async def main() -> None:
    load_dotenv(ROOT / ".env")
    client = HspClient(
        env("HSP_BASE_URL"), env("HSP_APP_KEY"), env("HSP_APP_SECRET"), env("HSP_MERCHANT_NAME"),
        Path(env("HSP_MERCHANT_PRIVATE_KEY_PATH")).read_bytes(),  # noqa: ASYNC240 - one-off CLI read
    )  # fmt: skip
    order_id = f"ZXQ-SMOKE-{int(time.time())}"
    contents = build_cart_contents(
        order_id, f"PAY-{order_id}", env("HSP_MERCHANT_NAME"), find_token(env("HSP_NETWORK"), env("HSP_COIN")),
        env("HSP_PAY_TO"), [("ZyroXQ smoke test", Decimal("1.00"))], datetime.now(UTC) + timedelta(hours=2),
    )  # fmt: skip
    try:
        order = await client.create_order(contents)
    except HspApiError as e:
        sys.exit(f"{e}\nHint: 10002 = HMAC/JWT problem, 10001 = cart validation, HTTP 403 HTML = Cloudflare block (ask HashKey to allowlist your IP)")
    print("Order created:", order)
    print("Status:", await client.get_payment(payment_request_id=order["payment_request_id"]))


if __name__ == "__main__":
    asyncio.run(main())
