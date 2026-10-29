"""Settlement gateways.

`SimulatedGateway` emulates HSP end to end: it keeps the Cart Mandate, verifies the signed Payment
Mandate the way the gateway must (signer, payee, amount, validity window, nonce reuse), records a
simulated transfer into the simulated escrow and delivers HMAC-signed webhooks through the real
verification path. Nothing it does moves real tokens.

`HostedGateway` is the real HSP merchant API. HSP's payer-side submission is not publicly documented,
so the advertiser signs in HSP's hosted checkout (`payment_url`) and the webhook reports the payment.

Neither gateway's word is proof of payment: DealService verifies the settlement transaction itself
(chain/settlement.py) before a deal is FUNDED.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from ..chain.escrow import SimulatedEscrow
from ..clock import Clock
from ..hsp.cart import cart_method, cart_total
from ..hsp.client import HspApiError, HspClient
from ..hsp.networks import TokenInfo
from ..hsp.webhook import sign_webhook
from .signer import PaymentMandate, recover_payment_mandate_signer, to_token_units

WebhookSink = Callable[[bytes, str], Awaitable[None]]

SIMULATED_APP_SECRET = "zyroxq-simulated-gateway-secret"


class OrderResult(BaseModel):
    payment_request_id: str
    payment_url: str


class GatewayRejected(Exception):
    """The gateway definitively refused: nothing was or will be settled for this authorization."""


class GatewayUnavailable(Exception):
    """Timeout, transport error or server error: the payment may or may not have gone through."""


class PaymentLookup(BaseModel):
    """What the gateway reports about a payment request. A claim to verify, not evidence."""

    status: str  # HSP payment status, e.g. payment-included | payment-finalized | payment-failed
    tx_hash: str | None = None
    payer: str | None = None


class Gateway(Protocol):
    name: str
    simulated: bool
    agent_signing: bool  # True → the advertiser's wallet signs the mandate here and we submit it
    webhook_secret: str

    async def create_order(self, contents: dict[str, Any]) -> OrderResult: ...
    async def submit_payment_mandate(self, mandate: PaymentMandate) -> str: ...
    async def lookup_payment(self, payment_request_id: str) -> PaymentLookup | None:
        """None when the gateway has no record (yet). Raises GatewayUnavailable when it cannot be asked."""
        ...


class SimulatedGateway:
    name = "simulated"
    simulated = True
    agent_signing = True
    webhook_secret = SIMULATED_APP_SECRET

    def __init__(self, token: TokenInfo, escrow: SimulatedEscrow, clock: Clock, base_url: str) -> None:
        self.token = token
        self.escrow = escrow
        self.clock = clock
        self.base_url = base_url.rstrip("/")
        self.sink: WebhookSink | None = None
        self._carts: dict[str, dict[str, Any]] = {}
        self._used_nonces: set[str] = set()
        self._payments: dict[str, PaymentLookup] = {}

    async def create_order(self, contents: dict[str, Any]) -> OrderResult:
        prid = contents["payment_request"]["details"]["id"]
        if prid in self._carts:
            raise GatewayRejected("10004 duplicate payment_request_id")
        self._carts[prid] = contents
        return OrderResult(payment_request_id=prid, payment_url=f"{self.base_url}/checkout/simulated/{prid}")

    async def submit_payment_mandate(self, mandate: PaymentMandate) -> str:
        contents = self._carts.get(mandate.payment_mandate_id)
        if contents is None:
            raise GatewayRejected("unknown payment_request_id")
        self._verify(mandate, contents)
        auth = mandate.authorization
        self._used_nonces.add(auth["nonce"])
        # The EIP-3009 transfer lands in pay_to, which is the escrow contract (simulated ledger entry)
        tx = self.escrow.receive_transfer(auth["from"], int(auth["value"]), auth["nonce"])
        for status in ("payment-included", "payment-finalized"):
            self._payments[mandate.payment_mandate_id] = PaymentLookup(status=status, tx_hash=tx, payer=mandate.payer)
            await self._emit(contents, mandate, status, tx)
        return tx

    async def lookup_payment(self, payment_request_id: str) -> PaymentLookup | None:
        return self._payments.get(payment_request_id)

    def _verify(self, mandate: PaymentMandate, contents: dict[str, Any]) -> None:
        auth = mandate.authorization
        method = cart_method(contents)
        now = int(self.clock.now().timestamp())
        checks = [
            (mandate.cart_mandate_id == contents["id"], "mandate references another cart"),
            (auth["to"].lower() == method["pay_to"].lower(), "authorization payee differs from cart pay_to"),
            (int(auth["value"]) == to_token_units(cart_total(contents), self.token.decimals), "authorized amount differs from cart total"),
            (int(auth["validAfter"]) <= now < int(auth["validBefore"]), "authorization outside its validity window"),
            (auth["nonce"] not in self._used_nonces, "authorization nonce already used"),
        ]
        for ok, reason in checks:
            if not ok:
                raise GatewayRejected(reason)
        signer = recover_payment_mandate_signer(mandate, self.token)
        if signer.lower() != auth["from"].lower() or signer.lower() != mandate.payer.lower():
            raise GatewayRejected("signature does not match payer")

    async def _emit(self, contents: dict[str, Any], mandate: PaymentMandate, status: str, tx: str) -> None:
        if self.sink is None:
            return
        method = cart_method(contents)
        amount = str(cart_total(contents))
        payload = {
            "event_type": "payment",
            "payment_request_id": mandate.payment_mandate_id,
            "request_id": f"sim-{mandate.authorization['nonce'][2:14]}",
            "cart_mandate_id": contents["id"],
            "payer_address": mandate.payer,
            "to_pay_address": method["pay_to"],
            "amount": amount,
            "order_amount": amount,
            "pay_amount": amount,
            "token": method["coin"],
            "token_address": method["contract_address"],
            "chain": f"eip155:{method['chain_id']}",
            "network": method["network"],
            "status": status,
            "tx_signature": tx,
            "created_at": self.clock.now().isoformat(),
        }
        raw = json.dumps(payload).encode()
        await self.sink(raw, sign_webhook(raw, self.webhook_secret, ts=int(self.clock.now().timestamp())))


class HostedGateway:
    name = "hosted"
    simulated = False
    agent_signing = False

    def __init__(self, client: HspClient, redirect_url: str | None = None) -> None:
        self.client = client
        self.redirect_url = redirect_url
        self.webhook_secret = client.app_secret

    async def create_order(self, contents: dict[str, Any]) -> OrderResult:
        data = await self.client.create_order(contents, self.redirect_url)
        return OrderResult(payment_request_id=data["payment_request_id"], payment_url=data["payment_url"])

    async def submit_payment_mandate(self, mandate: PaymentMandate) -> str:
        # Blocked on HashKey: the payer-side endpoint (/api/v1/payment/pay-mandate) and its payload are not
        # documented, so a browser-signed authorization cannot be submitted. Never reached: agent_signing is False.
        raise GatewayRejected("HSP payer-side submission is not public: sign in the hosted checkout (payment_url)")

    async def lookup_payment(self, payment_request_id: str) -> PaymentLookup | None:
        """GET /merchant/payments?payment_request_id=… (documented). The response is read with the same
        field names as the webhook payload; unknown shapes yield None so the deal stays unreconciled."""
        try:
            data = await self.client.get_payment(payment_request_id=payment_request_id)
        except HspApiError as e:
            if e.http_status >= 500 or e.code == -1:
                raise GatewayUnavailable(str(e)) from e
            return None
        except httpx.HTTPError as e:
            raise GatewayUnavailable(f"HSP unreachable: {type(e).__name__}") from e
        record = data[0] if isinstance(data, list) and data else data
        if not isinstance(record, dict) or not isinstance(record.get("status"), str):
            return None
        tx = record.get("tx_signature") or record.get("tx_hash")
        return PaymentLookup(status=record["status"], tx_hash=tx if isinstance(tx, str) else None, payer=record.get("payer_address"))
