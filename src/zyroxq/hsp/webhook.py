import hashlib
import hmac
import time

TERMINAL_STATUSES = frozenset({"payment-finalized", "payment-failed"})


def verify_webhook_signature(header: str | None, raw_body: bytes, app_secret: str, tolerance_seconds: int = 300, now: int | None = None) -> bool:
    """`X-Signature: t=<ts>,v1=<hex>` with v1 = HMAC-SHA256(app_secret, f"{t}.{raw_body}"). Pass the raw body."""
    if not header:
        return False
    parts = dict(p.strip().split("=", 1) for p in header.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
        received = parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else int(time.time())) - ts) > tolerance_seconds:
        return False
    expected = hmac.new(app_secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def sign_webhook(raw_body: bytes, app_secret: str, ts: int | None = None) -> str:
    """Builds the header HSP would send — used by the simulated gateway and tests."""
    ts = ts if ts is not None else int(time.time())
    v1 = hmac.new(app_secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"
