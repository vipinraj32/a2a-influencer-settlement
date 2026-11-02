"""Settlement gateways.

`SimulatedGateway` emulates HSP end to end: it keeps the Cart Mandate, verifies the signed Payment
Mandate the way the gateway must (signer, payee, amount, validity window, nonce reuse), records a
simulated transfer into the simulated escrow and delivers HMAC-signed webhooks through the real
verification path. Nothing it does moves real tokens.

`HostedGateway` is the real HSP merchant API. HSP's payer-side submission is not publicly documented,
so the advertiser signs in HSP's hosted checkout (`payment_url`) and the webhook reports the payment.

`ChainGateway` (PAYMENT_MODE=sepolia) is not HSP: it runs the same checks HSP must run on the signed Payment
Mandate, then relays the advertiser's EIP-3009 authorization to the token contract itself
(`transferWithAuthorization`, paid for by the operator key), which moves real test USDC from the advertiser's
wallet into the escrow. There is no webhook: the transaction hash is the only claim, and it is verified like any other.

Neither gateway's word is proof of payment: DealService verifies the settlement transaction itself
(chain/settlement.py) before a deal is FUNDED.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from eth_utils import to_checksum_address
from pydantic import BaseModel

from ..chain.escrow import SimulatedEscrow
from ..chain.sender import BroadcastUnknown, ChainSender, TxRejected
from ..clock import Clock
from ..hsp.cart import cart_method, cart_total
from ..hsp.client import HspApiError, HspClient
from ..hsp.networks import TokenInfo
from ..hsp.webhook import sign_webhook
from .signer import PaymentMandate, recover_payment_mandate_signer, to_token_units

WebhookSink = Callable[[bytes, str], Awaitable[None]]

SIMULATED_APP_SECRET = "zyroxq-simulated-gateway-secret"


TRANSFER_WITH_AUTHORIZATION_ABI = [
    {
        "name": "transferWithAuthorization",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "v", "type": "uint8"},
            {"name": "r", "type": "bytes32"},
            {"name": "s", "type": "bytes32"},
        ],
        "outputs": [],
    }
]


def verify_mandate(mandate: PaymentMandate, contents: dict[str, Any], token: TokenInfo, now: int, used_nonces: set[str]) -> None:
    """What the gateway must check before it moves money: the right cart, payee, amount, window and signer."""
    auth = mandate.authorization
    method = cart_method(contents)
    checks = [
        (mandate.cart_mandate_id == contents["id"], "mandate references another cart"),
        (auth["to"].lower() == method["pay_to"].lower(), "authorization payee differs from cart pay_to"),
        (int(auth["value"]) == to_token_units(cart_total(contents), token.decimals), "authorized amount differs from cart total"),
        (int(auth["validAfter"]) <= now < int(auth["validBefore"]), "authorization outside its validity window"),
        (auth["nonce"] not in used_nonces, "authorization nonce already used"),
    ]
    for ok, reason in checks:
        if not ok:
            raise GatewayRejected(reason)
    signer = recover_payment_mandate_signer(mandate, token)
    if signer.lower() != auth["from"].lower() or signer.lower() != mandate.payer.lower():
        raise GatewayRejected("signature does not match payer")


class OrderResult(BaseModel):
    payment_request_id: str
    payment_url: str


class GatewayRejected(Exception):
    """The gateway definitively refused: nothing was or will be settled for this authorization."""


class GatewayUnavailable(Exception):
    """Timeout, transport error or server error: the payment may or may not have gone through."""

    def __init__(self, message: str, tx_hash: str | None = None) -> None:
        super().__init__(message)
        self.tx_hash = tx_hash  # set when the transaction was signed (so its hash is known) but its broadcast is unconfirmed


class PaymentLookup(BaseModel):
    """What the gateway reports about a payment request. A claim to verify, not evidence."""

    status: str  # HSP payment status, e.g. payment-included | payment-finalized | payment-failed
    tx_hash: str | None = None
    payer: str | None = None


class Gateway(Protocol):
    name: str
    simulated: bool
    agent_signing: bool  # True → the advertiser's wallet signs the mandate here and we submit it
    direct_onchain: bool  # True → no gateway reports back: the submitted transaction hash is the only claim
    webhook_secret: str

    async def create_order(self, contents: dict[str, Any]) -> OrderResult: ...
    async def submit_payment_mandate(self, mandate: PaymentMandate, contents: dict[str, Any] | None = None) -> str: ...
    async def lookup_payment(self, payment_request_id: str) -> PaymentLookup | None:
        """None when the gateway has no record (yet). Raises GatewayUnavailable when it cannot be asked."""
        ...


class SimulatedGateway:
    name = "simulated"
    simulated = True
    agent_signing = True
    direct_onchain = False
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

    async def submit_payment_mandate(self, mandate: PaymentMandate, contents: dict[str, Any] | None = None) -> str:
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
        verify_mandate(mandate, contents, self.token, int(self.clock.now().timestamp()), self._used_nonces)

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


class ChainGateway:
    """PAYMENT_MODE=sepolia: verifies the signed Payment Mandate like HSP would, then submits the EIP-3009
    authorization to the token on chain. Real test USDC moves from the advertiser's wallet to the escrow; the
    operator only pays gas and can neither change the amount nor the recipient (both are signed)."""

    name = "sepolia"
    simulated = False
    agent_signing = True
    direct_onchain = True

    def __init__(self, token: TokenInfo, sender: ChainSender, clock: Clock) -> None:
        self.token = token
        self.sender = sender
        self.clock = clock
        self.webhook_secret = secrets.token_hex(32)  # nothing sends webhooks in this mode: every signature fails
        self.contract = sender.w3.eth.contract(address=to_checksum_address(token.contract_address), abi=TRANSFER_WITH_AUTHORIZATION_ABI)
        self._payments: dict[str, PaymentLookup] = {}

    async def create_order(self, contents: dict[str, Any]) -> OrderResult:
        prid = contents["payment_request"]["details"]["id"]
        return OrderResult(payment_request_id=prid, payment_url="")

    async def submit_payment_mandate(self, mandate: PaymentMandate, contents: dict[str, Any] | None = None) -> str:
        if contents is None:
            raise GatewayRejected("the Cart Mandate is required to check the authorization")
        # the token rejects a reused nonce itself; DealService also claims each nonce once
        verify_mandate(mandate, contents, self.token, int(self.clock.now().timestamp()), set())
        auth = mandate.authorization
        sig = bytes.fromhex(mandate.x402["payload"]["signature"].removeprefix("0x"))
        v, r, s_ = sig[64] if sig[64] >= 27 else sig[64] + 27, sig[:32], sig[32:64]
        fn = self.contract.functions.transferWithAuthorization(
            to_checksum_address(auth["from"]), to_checksum_address(auth["to"]), int(auth["value"]), int(auth["validAfter"]),
            int(auth["validBefore"]), bytes.fromhex(auth["nonce"].removeprefix("0x")), v, r, s_,
        )  # fmt: skip
        try:
            tx = await asyncio.to_thread(self.sender.submit, fn)
        except TxRejected as e:  # the token would refuse it (balance, signature, used nonce): nothing was sent
            raise GatewayRejected(f"the token refused the authorization: {e}") from e
        except BroadcastUnknown as e:
            raise GatewayUnavailable(f"broadcast unconfirmed: {e}", tx_hash=e.tx_hash) from e
        self._payments[mandate.payment_mandate_id] = PaymentLookup(status="payment-included", tx_hash=tx, payer=mandate.payer)
        return tx

    async def lookup_payment(self, payment_request_id: str) -> PaymentLookup | None:
        # Only this process's own submissions; after a restart the payment attempt's tx hash is checked on chain instead
        return self._payments.get(payment_request_id)


class HostedGateway:
    name = "hosted"
    simulated = False
    agent_signing = False
    direct_onchain = False

    def __init__(self, client: HspClient, redirect_url: str | None = None) -> None:
        self.client = client
        self.redirect_url = redirect_url
        self.webhook_secret = client.app_secret

    async def create_order(self, contents: dict[str, Any]) -> OrderResult:
        data = await self.client.create_order(contents, self.redirect_url)
        return OrderResult(payment_request_id=data["payment_request_id"], payment_url=data["payment_url"])

    async def submit_payment_mandate(self, mandate: PaymentMandate, contents: dict[str, Any] | None = None) -> str:
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
