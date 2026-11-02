from __future__ import annotations

import json
import secrets
from decimal import Decimal
from typing import Literal

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from ..chain.escrow import attestation_digest
from ..models import Attestation, ProofCheck
from ..wallet.signer import to_token_units


class Attestor:
    """Holds the verifier key. Signs a release only for checks that already passed."""

    def __init__(self, private_key: str, escrow_address: str, chain_id: int, token_decimals: int = 6) -> None:
        self._key = private_key
        self.address = Account.from_key(private_key).address
        self.escrow_address = escrow_address
        self.chain_id = chain_id
        self.decimals = token_decimals

    def attest(
        self,
        deal_id: str,
        video_id: str,
        payee: str,
        amount: Decimal,
        kind: Literal["fixed", "performance"],
        checks: list[ProofCheck],
        views: int | None = None,
        nonce: int | None = None,
    ) -> Attestation:
        if not checks or not all(c.passed for c in checks):
            raise ValueError("refusing to attest: not all checks passed")
        # One nonce per payout decision. DealService persists it with the decision (PayoutIntent) and reuses it on every
        # retry, so a release resent after a lost response is the same attestation, which the escrow accepts once.
        nonce = secrets.randbits(64) if nonce is None else nonce
        evidence = json.dumps({"deal_id": deal_id, "video_id": video_id, "views": views, "checks": [c.model_dump() for c in checks]}, sort_keys=True)
        digest = attestation_digest(self.escrow_address, self.chain_id, deal_id, payee, to_token_units(amount, self.decimals), nonce)
        signature = Account.sign_message(encode_defunct(primitive=digest), private_key=self._key).signature
        return Attestation(
            deal_id=deal_id,
            video_id=video_id,
            payee=payee,
            amount=amount,
            kind=kind,
            nonce=nonce,
            evidence_hash="0x" + keccak(text=evidence).hex(),
            verifier=self.address,
            signature="0x" + signature.hex().removeprefix("0x"),
        )
