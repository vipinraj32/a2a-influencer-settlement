"""Wallet signatures. These run in fixed graph nodes after the policy gate — never as LLM tools."""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from pydantic import BaseModel

from ..hsp.cart import cart_method, cart_total
from ..hsp.networks import TokenInfo
from ..models import DealTerms

TRANSFER_WITH_AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


class PaymentMandate(BaseModel):
    """The payer's authorization for a Cart Mandate: an x402 `exact` scheme payload (EIP-3009).

    HSP's payer-side submission format is not publicly documented; this follows the x402 v2 spec,
    which the Cart Mandate's `supported_methods` points at.
    """

    payment_mandate_id: str  # == payment_request_id (ID4 = ID2)
    cart_mandate_id: str
    payer: str
    x402: dict[str, Any]

    @property
    def authorization(self) -> dict[str, Any]:
        return self.x402["payload"]["authorization"]


def token_domain(token: TokenInfo, name: str = "USD Coin", version: str = "2") -> dict[str, Any]:
    return {"name": name, "version": version, "chainId": token.chain_id, "verifyingContract": token.contract_address}


def to_token_units(amount: Decimal, decimals: int) -> int:
    units = amount * (Decimal(10) ** decimals)
    if units != units.to_integral_value():
        raise ValueError(f"{amount} is not representable with {decimals} decimals")
    return int(units)


def sign_payment_mandate(
    private_key: str,
    cart_contents: dict[str, Any],
    payment_request_id: str,
    token: TokenInfo,
    valid_after: int,
    valid_before: int,
    domain_name: str = "USD Coin",
    domain_version: str = "2",
) -> PaymentMandate:
    account = Account.from_key(private_key)
    method = cart_method(cart_contents)
    authorization = {
        "from": account.address,
        "to": method["pay_to"],
        "value": to_token_units(cart_total(cart_contents), token.decimals),
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": "0x" + secrets.token_hex(32),
    }
    signed = Account.sign_typed_data(
        private_key,
        domain_data=token_domain(token, domain_name, domain_version),
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=authorization,
    )
    payload = {
        "x402Version": 2,
        "scheme": "exact",
        "network": f"eip155:{token.chain_id}",
        "payload": {
            "signature": "0x" + signed.signature.hex().removeprefix("0x"),
            "authorization": {k: str(v) for k, v in authorization.items()},
        },
    }
    return PaymentMandate(
        payment_mandate_id=payment_request_id,
        cart_mandate_id=cart_contents["id"],
        payer=account.address,
        x402=payload,
    )


def recover_payment_mandate_signer(
    mandate: PaymentMandate, token: TokenInfo, domain_name: str = "USD Coin", domain_version: str = "2"
) -> str:
    auth = mandate.authorization
    message = {
        "from": auth["from"],
        "to": auth["to"],
        "value": int(auth["value"]),
        "validAfter": int(auth["validAfter"]),
        "validBefore": int(auth["validBefore"]),
        "nonce": auth["nonce"],
    }
    encoded = encode_typed_data(
        domain_data=token_domain(token, domain_name, domain_version),
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=message,
    )
    return Account.recover_message(encoded, signature=mandate.x402["payload"]["signature"])


def sign_terms(private_key: str, terms: DealTerms) -> DealTerms:
    """Influencer agent signs the deal terms hash (EIP-191)."""
    signed = Account.sign_message(encode_defunct(hexstr=terms.terms_hash()), private_key=private_key)
    return terms.model_copy(update={"influencer_signature": "0x" + signed.signature.hex().removeprefix("0x")})
