import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """HSP Canonical JSON: keys sorted recursively, arrays keep order, compact, UTF-8 (no \\u escapes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
