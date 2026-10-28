"""HSP authentication: HMAC-SHA256 request signing and the ES256K `merchant_authorization` JWT."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .canonical import canonical_json, sha256_hex

# secp256k1 group order; JWT signatures are normalized to low-S
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def hmac_message(method: str, path: str, query: str, body: str | None, timestamp: int, nonce: str) -> str:
    """`METHOD\\nPATH\\nQUERY\\nbodyHash\\ntimestamp\\nnonce`; bodyHash is empty without a body."""
    body_hash = sha256_hex(body) if body else ""
    return "\n".join([method.upper(), path, query, body_hash, str(timestamp), nonce])


def sign_request(
    app_key: str,
    app_secret: str,
    method: str,
    path: str,
    query: str = "",
    body: str | None = None,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = timestamp if timestamp is not None else int(time.time())
    nonce = nonce or secrets.token_hex(16)
    message = hmac_message(method, path, query, body, ts, nonce)
    signature = hmac.new(app_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {"X-App-Key": app_key, "X-Signature": signature, "X-Timestamp": str(ts), "X-Nonce": nonce}


def load_private_key(pem: str | bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem if isinstance(pem, bytes) else pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256K1):
        raise ValueError("HSP merchant key must be a secp256k1 EC private key")
    return key


def cart_hash(contents: Any) -> str:
    return sha256_hex(canonical_json(contents))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_merchant_jwt(
    key: ec.EllipticCurvePrivateKey,
    merchant_name: str,
    cart_hash_hex: str,
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    iat = now if now is not None else int(time.time())
    header = {"alg": "ES256K", "typ": "JWT"}
    claims = {
        "iss": merchant_name,
        "sub": merchant_name,
        "aud": "HashkeyMerchant",
        "iat": iat,
        "exp": iat + ttl_seconds,
        "jti": f"JWT-{iat}-{secrets.token_hex(6)}",
        "cart_hash": cart_hash_hex,
    }
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}.{_b64url(json.dumps(claims, separators=(',', ':')).encode())}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    if s > SECP256K1_N // 2:
        s = SECP256K1_N - s
    # JWS wants raw r||s, 32 bytes each
    return f"{signing_input}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def generate_merchant_key() -> tuple[bytes, bytes]:
    """Returns (private PEM PKCS8, public PEM SPKI) for HSP onboarding."""
    key = ec.generate_private_key(ec.SECP256K1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public
