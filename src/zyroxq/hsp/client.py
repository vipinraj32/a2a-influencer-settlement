from typing import Any
from urllib.parse import urlencode

import httpx

from .auth import cart_hash, load_private_key, sign_merchant_jwt, sign_request
from .canonical import canonical_json

API_PREFIX = "/api/v1"


class HspApiError(Exception):
    def __init__(self, code: int, http_status: int, msg: str) -> None:
        super().__init__(f"HSP error {code} (HTTP {http_status}): {msg}")
        self.code = code
        self.http_status = http_status


class HspClient:
    """HSP merchant API: create Cart Mandate orders and query payments."""

    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        merchant_name: str,
        merchant_private_key_pem: str | bytes,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.merchant_name = merchant_name
        self._key = load_private_key(merchant_private_key_pem)
        self._http = http or httpx.AsyncClient(timeout=30)

    def sign_cart_mandate(self, contents: dict[str, Any], redirect_url: str | None = None) -> dict[str, Any]:
        jwt = sign_merchant_jwt(self._key, self.merchant_name, cart_hash(contents))
        body: dict[str, Any] = {"cart_mandate": {"contents": contents, "merchant_authorization": jwt}}
        if redirect_url:
            body["redirect_url"] = redirect_url
        return body

    async def create_order(self, contents: dict[str, Any], redirect_url: str | None = None) -> dict[str, Any]:
        return await self._request("POST", "/merchant/orders", body=self.sign_cart_mandate(contents, redirect_url))

    async def create_reusable_order(self, contents: dict[str, Any], redirect_url: str | None = None) -> dict[str, Any]:
        return await self._request("POST", "/merchant/orders/reusable", body=self.sign_cart_mandate(contents, redirect_url))

    async def get_payment(self, *, payment_request_id: str | None = None, flow_id: str | None = None) -> dict[str, Any]:
        if (payment_request_id is None) == (flow_id is None):
            raise ValueError("Pass exactly one of payment_request_id or flow_id")
        query = {"payment_request_id": payment_request_id} if payment_request_id else {"flow_id": flow_id}
        return await self._request("GET", "/merchant/payments", query=query)

    async def list_payments_by_cart(self, cart_mandate_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/merchant/payments", query={"cart_mandate_id": cart_mandate_id})

    async def list_reusable_payments(self, cart_mandate_id: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        query = {"cart_mandate_id": cart_mandate_id, "page": page, "page_size": page_size}
        return await self._request("GET", "/merchant/payments/reusable", query=query)

    async def _request(self, method: str, route: str, *, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        path = API_PREFIX + route
        qs = urlencode(query) if query else ""
        # Send the canonical string itself so the gateway's body hash matches ours byte for byte
        raw = canonical_json(body) if body is not None else None
        headers = sign_request(self.app_key, self.app_secret, method, path, qs, raw)
        if raw is not None:
            headers["Content-Type"] = "application/json"
        url = self.base_url + path + (f"?{qs}" if qs else "")
        res = await self._http.request(method, url, headers=headers, content=raw.encode() if raw else None)
        try:
            envelope = res.json()
        except ValueError:
            raise HspApiError(-1, res.status_code, f"Non-JSON response: {res.text[:200]}") from None
        if envelope.get("code") != 0:
            raise HspApiError(envelope.get("code", -1), res.status_code, envelope.get("msg", ""))
        return envelope.get("data")
