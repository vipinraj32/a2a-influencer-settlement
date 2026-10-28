import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from zyroxq.hsp import (
    HspApiError,
    HspClient,
    build_cart_contents,
    canonical_json,
    cart_hash,
    find_token,
    generate_merchant_key,
    hmac_message,
    load_private_key,
    sign_merchant_jwt,
    sign_request,
    sign_webhook,
    verify_webhook_signature,
)
from zyroxq.hsp.auth import SECP256K1_N


def b64d(s: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_canonical_json_matches_docs():
    assert canonical_json({"b": 2, "a": 1, "nested": {"z": "last", "m": "middle"}}) == '{"a":1,"b":2,"nested":{"m":"middle","z":"last"}}'


def test_hmac_message_get_has_empty_body_hash():
    msg = hmac_message("get", "/api/v1/merchant/payments", "cart_mandate_id=ORDER-123", None, 1709123456, "a3f1b2c4d5e6")
    assert msg == "GET\n/api/v1/merchant/payments\ncart_mandate_id=ORDER-123\n\n1709123456\na3f1b2c4d5e6"


def test_sign_request_is_hmac_of_message():
    h = sign_request("ak", "secret", "POST", "/api/v1/merchant/orders", body='{"a":1}', timestamp=1, nonce="n")
    msg = hmac_message("POST", "/api/v1/merchant/orders", "", '{"a":1}', 1, "n")
    assert h["X-Signature"] == hmac.new(b"secret", msg.encode(), hashlib.sha256).hexdigest()


def test_merchant_jwt_es256k_low_s_and_verifiable():
    private_pem, _ = generate_merchant_key()
    key = load_private_key(private_pem)
    digest = cart_hash({"id": "cart-123"})
    for _ in range(20):
        jwt = sign_merchant_jwt(key, "ZyroXQ", digest, now=1000)
        h, p, s = jwt.split(".")
        assert json.loads(b64d(h)) == {"alg": "ES256K", "typ": "JWT"}
        claims = json.loads(b64d(p))
        assert claims["aud"] == "HashkeyMerchant" and claims["cart_hash"] == digest and claims["exp"] == 4600
        sig = b64d(s)
        assert len(sig) == 64
        r, s_val = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
        assert s_val <= SECP256K1_N // 2
        key.public_key().verify(encode_dss_signature(r, s_val), f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))


def test_rejects_non_secp256k1_key():
    from cryptography.hazmat.primitives import serialization

    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    with pytest.raises(ValueError):
        load_private_key(pem)


def make_cart(items=(("Fixed fee", Decimal("20.10")), ("Performance cap", Decimal("0.20")))):
    return build_cart_contents(
        "ZXQ-1", "PAY-1", "ZyroXQ", find_token("hashkey-testnet", "USDC"), "0x" + "01" * 20, list(items),
        datetime(2026, 11, 1, tzinfo=UTC),
    )


def test_cart_totals_and_fields():
    c = make_cart()
    assert c["payment_request"]["details"]["total"]["amount"]["value"] == "20.30"
    assert c["cart_expiry"] == "2026-11-01T00:00:00Z"
    assert c["payment_request"]["method_data"][0]["data"]["chain_id"] == 133


def test_cart_rejects_sub_cent_amount():
    with pytest.raises(ValueError):
        make_cart([("x", Decimal("1.001"))])


def test_webhook_signature_roundtrip():
    body = b'{"status":"payment-finalized"}'
    header = sign_webhook(body, "secret", ts=1_700_000_000)
    assert verify_webhook_signature(header, body, "secret", now=1_700_000_010)
    assert not verify_webhook_signature(header, body + b" ", "secret", now=1_700_000_010)
    assert not verify_webhook_signature(header, body, "secret", now=1_700_000_301)
    assert not verify_webhook_signature(None, body, "secret")


@respx.mock
async def test_client_sends_canonical_body_with_matching_hmac():
    route = respx.post("https://gw.test/api/v1/merchant/orders").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "msg": "success", "data": {"payment_request_id": "PAY-1", "payment_url": "https://x", "multi_pay": False}}
        )
    )
    private_pem, _ = generate_merchant_key()
    client = HspClient("https://gw.test/", "ak", "secret", "ZyroXQ", private_pem)
    contents = make_cart()
    contents["cart_expiry"] = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = await client.create_order(contents)
    assert result["payment_url"] == "https://x"

    req = route.calls.last.request
    body = req.content.decode()
    assert body == canonical_json(json.loads(body))
    expected = sign_request("ak", "secret", "POST", "/api/v1/merchant/orders", body=body,
                            timestamp=int(req.headers["X-Timestamp"]), nonce=req.headers["X-Nonce"])
    assert req.headers["X-Signature"] == expected["X-Signature"]
    # merchant JWT binds this exact cart
    jwt = json.loads(body)["cart_mandate"]["merchant_authorization"]
    assert json.loads(b64d(jwt.split(".")[1]))["cart_hash"] == cart_hash(json.loads(body)["cart_mandate"]["contents"])


@respx.mock
async def test_client_raises_on_error_code():
    respx.get("https://gw.test/api/v1/merchant/payments").mock(return_value=httpx.Response(400, json={"code": 40001, "msg": "Invalid mandate state"}))
    private_pem, _ = generate_merchant_key()
    client = HspClient("https://gw.test", "ak", "s", "Z", private_pem)
    with pytest.raises(HspApiError) as e:
        await client.get_payment(flow_id="f")
    assert e.value.code == 40001
