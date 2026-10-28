"""Wallet linking by EIP-191 signature. For influencers the same signature authorizes the agent key
that signs DealTerms (policy.delegation_valid checks the "Agent signer" and "Channel" lines)."""

from __future__ import annotations

import secrets
from datetime import datetime

from web3 import Web3

from ..models import User


def link_message(user: User, address: str, now: datetime) -> str:
    lines = [
        "ZyroXQ wallet link",
        f"Account: {user.email}",
        f"Role: {user.role}",
        f"Wallet: {Web3.to_checksum_address(address)}",
    ]
    if user.role == "influencer" and user.channel:
        lines += [
            f"Channel: {user.channel.channel_id}",
            f"Agent signer: {user.agent_signer}",
            "I authorize this agent key to sign ZyroXQ deal terms for my channel. Payouts go to this wallet.",
        ]
    else:
        lines.append("Payment Mandates for my campaigns are signed by this wallet.")
    lines += [f"Nonce: {secrets.token_hex(8)}", f"Issued: {now.isoformat(timespec='seconds')}"]
    return "\n".join(lines)
