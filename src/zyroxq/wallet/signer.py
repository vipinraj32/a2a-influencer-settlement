"""Wallet signatures. These run in fixed graph nodes after the policy gate — never as LLM tools."""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from pydantic import BaseModel
from web3 import Web3

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


def payment_authorization(payer: str, cart_contents: dict[str, Any], token: TokenInfo, valid_after: int, valid_before: int) -> dict[str, str]:
    """The EIP-3009 TransferWithAuthorization for a Cart Mandate: pays the cart total to its pay_to."""
    method = cart_method(cart_contents)
    authorization = {
        "from": Web3.to_checksum_address(payer),
        "to": method["pay_to"],
        "value": to_token_units(cart_total(cart_contents), token.decimals),
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": "0x" + secrets.token_hex(32),
    }
    return {k: str(v) for k, v in authorization.items()}


def _typed_message(authorization: dict[str, str]) -> dict[str, Any]:
    return {**authorization, **{k: int(authorization[k]) for k in ("value", "validAfter", "validBefore")}}


def payment_typed_data(authorization: dict[str, str], token: TokenInfo, domain_name: str = "USD Coin", domain_version: str = "2") -> dict[str, Any]:
    """Full EIP-712 document for a browser wallet's `eth_signTypedData_v4` (uint256 values as decimal strings)."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **TRANSFER_WITH_AUTHORIZATION_TYPES,
        },
        "primaryType": "TransferWithAuthorization",
        "domain": token_domain(token, domain_name, domain_version),
        "message": authorization,
    }


def mandate_from_signature(
    authorization: dict[str, str], signature: str, cart_contents: dict[str, Any], payment_request_id: str, token: TokenInfo
) -> PaymentMandate:
    payload = {
        "x402Version": 2,
        "scheme": "exact",
        "network": f"eip155:{token.chain_id}",
        "payload": {"signature": signature, "authorization": dict(authorization)},
    }
    return PaymentMandate(payment_mandate_id=payment_request_id, cart_mandate_id=cart_contents["id"], payer=authorization["from"], x402=payload)


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
    """Signs with a local key — what a browser wallet does for `payment_typed_data` (used by tests and scripts)."""
    authorization = payment_authorization(Account.from_key(private_key).address, cart_contents, token, valid_after, valid_before)
    signed = Account.sign_typed_data(
        private_key,
        domain_data=token_domain(token, domain_name, domain_version),
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=_typed_message(authorization),
    )
    return mandate_from_signature(authorization, "0x" + signed.signature.hex().removeprefix("0x"), cart_contents, payment_request_id, token)


def recover_payment_mandate_signer(mandate: PaymentMandate, token: TokenInfo, domain_name: str = "USD Coin", domain_version: str = "2") -> str:
    encoded = encode_typed_data(
        domain_data=token_domain(token, domain_name, domain_version),
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=_typed_message(mandate.authorization),
    )
    try:
        return Account.recover_message(encoded, signature=mandate.x402["payload"]["signature"])
    except (ValueError, TypeError):
        return ""  # malformed signature from the browser


def recover_personal_sign(message: str, signature: str) -> str | None:
    """Signer of an EIP-191 `personal_sign` text message (wallet linking, agent delegation)."""
    try:
        return Account.recover_message(encode_defunct(text=message), signature=signature)
    except (ValueError, TypeError):
        return None


def sign_terms(private_key: str, terms: DealTerms) -> DealTerms:
    """Influencer agent signs the deal terms hash (EIP-191)."""
    signed = Account.sign_message(encode_defunct(hexstr=terms.terms_hash()), private_key=private_key)
    return terms.model_copy(update={"influencer_signature": "0x" + signed.signature.hex().removeprefix("0x")})
