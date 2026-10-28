"""Settlement gateways.

`SimulatedGateway` emulates HSP end to end: it keeps the Cart Mandate, verifies the signed Payment
Mandate the way the gateway must (signer, payee, amount, validity window, nonce reuse), moves funds
into the simulated escrow and delivers HMAC-signed webhooks through the real verification path.

`HostedGateway` is the real HSP merchant API. HSP's payer-side submission is not publicly documented,
so the advertiser signs in HSP's hosted checkout (`payment_url`) and the webhook settles the deal.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import BaseModel

from ..chain.escrow import SimulatedEscrow
from ..clock import Clock
from ..hsp.cart import cart_method, cart_total
from ..hsp.client import HspClient
from ..hsp.networks import TokenInfo
from ..hsp.webhook import sign_webhook
from .signer import PaymentMandate, recover_payment_mandate_signer, to_token_units

WebhookSink = Callable[[bytes, str], Awaitable[None]]

SIMULATED_APP_SECRET = "zyroxq-simulated-gateway-secret"


class OrderResult(BaseModel):
    payment_request_id: str
    payment_url: str


class GatewayRejected(Exception):
    pass


class Gateway(Protocol):
    name: str
    agent_signing: bool  # True → the Payment Agent signs and submits the mandate itself
    webhook_secret: str

    async def create_order(self, contents: dict[str, Any]) -> OrderResult: ...
    async def submit_payment_mandate(self, mandate: PaymentMandate) -> str: ...


class SimulatedGateway:
    name = "simulated"
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
        tx = "0xsim" + secrets.token_hex(30)
        # The EIP-3009 transfer lands in pay_to, which is the escrow contract
        self.escrow.credit(int(auth["value"]))
        for status in ("payment-included", "payment-finalized"):
            await self._emit(contents, mandate, status, tx)
        return tx

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
    agent_signing = False

    def __init__(self, client: HspClient, redirect_url: str | None = None) -> None:
        self.client = client
        self.redirect_url = redirect_url
        self.webhook_secret = client.app_secret

    async def create_order(self, contents: dict[str, Any]) -> OrderResult:
        data = await self.client.create_order(contents, self.redirect_url)
        return OrderResult(payment_request_id=data["payment_request_id"], payment_url=data["payment_url"])

    async def submit_payment_mandate(self, mandate: PaymentMandate) -> str:
        raise GatewayRejected("HSP payer-side submission is not public: sign in the hosted checkout (payment_url)")
