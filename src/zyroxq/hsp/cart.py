"""Cart Mandate `contents` builder. Amounts are USD decimal strings; the gateway rejects
(error 10001) carts whose display items don't sum to the total, so totals are computed in Decimal."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .networks import TokenInfo

X402_METHOD = "https://www.x402.org/"


def usd(value: Decimal) -> dict[str, str]:
    return {"currency": "USD", "value": f"{value.quantize(Decimal('0.01'))}"}


def build_cart_contents(
    cart_mandate_id: str,
    payment_request_id: str,
    merchant_name: str,
    token: TokenInfo,
    pay_to: str,
    items: list[tuple[str, Decimal]],
    expires_at: datetime,
    user_confirmation_required: bool = True,
) -> dict[str, Any]:
    if not items:
        raise ValueError("Cart needs at least one item")
    for label, amount in items:
        if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError(f"Invalid USD amount for {label!r}: {amount}")
    total = sum((a for _, a in items), Decimal("0"))
    return {
        "id": cart_mandate_id,
        "user_cart_confirmation_required": user_confirmation_required,
        "payment_request": {
            "method_data": [
                {
                    "supported_methods": X402_METHOD,
                    "data": {
                        "x402Version": 2,
                        "network": token.network,
                        "chain_id": token.chain_id,
                        "contract_address": token.contract_address,
                        "pay_to": pay_to,
                        "coin": token.coin,
                    },
                }
            ],
            "details": {
                "id": payment_request_id,
                "display_items": [{"label": label, "amount": usd(a)} for label, a in items],
                "total": {"label": "Total", "amount": usd(total)},
            },
        },
        "cart_expiry": expires_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "merchant_name": merchant_name,
    }


def cart_total(contents: dict[str, Any]) -> Decimal:
    return Decimal(contents["payment_request"]["details"]["total"]["amount"]["value"])


def cart_method(contents: dict[str, Any]) -> dict[str, Any]:
    return contents["payment_request"]["method_data"][0]["data"]
