"""Influencer agent keys are generated server-side (the agent signs DealTerms while the creator is
offline) and stored encrypted with a key derived from SESSION_SECRET. The payout wallet stays in
the creator's browser wallet and only authorizes the agent key once, by signature."""

from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet
from eth_account import Account


def _fernet(secret: str) -> Fernet:
    key = hashlib.sha256(f"zyroxq-agent-keys:{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def new_agent_key(secret: str) -> tuple[str, str]:
    """Returns (address, encrypted private key)."""
    private_key = "0x" + secrets.token_hex(32)
    return Account.from_key(private_key).address, _fernet(secret).encrypt(private_key.encode()).decode()


def decrypt_agent_key(secret: str, encrypted: str) -> str:
    return _fernet(secret).decrypt(encrypted.encode()).decode()
