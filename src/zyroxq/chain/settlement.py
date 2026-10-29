"""Independent settlement evidence. A webhook, an HTTP 200 or a browser callback only *claims* a payment
happened; a deal is FUNDED only after the settlement transaction itself proves an exact transfer of the
right token from the payer into the escrow.

`OnchainSettlementVerifier` reads the transaction receipt from HashKey Chain. `SimulatedSettlementVerifier`
reads the receipts `SimulatedEscrow` records, and every result it returns is marked simulated."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from eth_utils import keccak, to_checksum_address

from .escrow import SimulatedEscrow, tx_key

TRANSFER_TOPIC = keccak(text="Transfer(address,address,uint256)")
AUTHORIZATION_STATE_ABI = [
    {
        "name": "authorizationState",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "authorizer", "type": "address"}, {"name": "nonce", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    }
]


class SettlementPending(Exception):
    """No final evidence yet (receipt not found or not enough confirmations): check again later."""


class SettlementMismatch(Exception):
    """The transaction exists but does not prove this payment (reverted, wrong token/payer/payee/amount)."""


@dataclass(frozen=True)
class FundingEvidence:
    tx_hash: str
    payer: str
    escrow: str
    token: str
    value_units: int
    chain_id: int
    block_number: int | None
    confirmations: int
    simulated: bool


class SettlementVerifier(Protocol):
    simulated: bool

    def verify_funding(self, tx_hash: str, payer: str, escrow: str, value_units: int) -> FundingEvidence: ...
    def authorization_used(self, payer: str, nonce: str) -> bool:
        """EIP-3009 `authorizationState`: True once the token has consumed this authorization."""
        ...


def _topic_address(topic: Any) -> str:
    return to_checksum_address(bytes(topic)[-20:])


def find_transfer(logs: list[Any], token: str, payer: str, escrow: str, value_units: int) -> bool:
    """True if the logs contain an ERC-20 Transfer(payer → escrow, value) emitted by `token`."""
    token, payer, escrow = (to_checksum_address(a) for a in (token, payer, escrow))
    for log in logs:
        topics = log["topics"]
        if to_checksum_address(log["address"]) != token or len(topics) != 3 or bytes(topics[0]) != TRANSFER_TOPIC:
            continue
        data = log["data"]
        raw = bytes.fromhex(data.removeprefix("0x")) if isinstance(data, str) else bytes(data)
        if _topic_address(topics[1]) == payer and _topic_address(topics[2]) == escrow and int.from_bytes(raw, "big") == value_units:
            return True
    return False


class OnchainSettlementVerifier:
    simulated = False

    def __init__(self, rpc_url: str, token: str, chain_id: int, min_confirmations: int = 1) -> None:
        from web3 import Web3

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.token = to_checksum_address(token)
        self.chain_id = chain_id
        self.min_confirmations = max(1, min_confirmations)

    def verify_funding(self, tx_hash: str, payer: str, escrow: str, value_units: int) -> FundingEvidence:
        from web3.exceptions import TransactionNotFound

        tx_key(tx_hash)  # must be a well-formed 32-byte hash
        if self.w3.eth.chain_id != self.chain_id:
            raise SettlementMismatch(f"RPC is on chain {self.w3.eth.chain_id}, expected {self.chain_id}")
        try:
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            raise SettlementPending("settlement transaction not found on chain yet") from None
        if receipt["status"] != 1:
            raise SettlementMismatch("settlement transaction reverted")
        confirmations = self.w3.eth.block_number - receipt["blockNumber"] + 1
        if confirmations < self.min_confirmations:
            raise SettlementPending(f"{confirmations}/{self.min_confirmations} confirmations")
        if not find_transfer(receipt["logs"], self.token, payer, escrow, value_units):
            raise SettlementMismatch("transaction has no Transfer of the exact amount from the payer to the escrow")
        return FundingEvidence(tx_hash, to_checksum_address(payer), to_checksum_address(escrow), self.token, value_units,
                               self.chain_id, receipt["blockNumber"], confirmations, simulated=False)  # fmt: skip

    def authorization_used(self, payer: str, nonce: str) -> bool:
        token = self.w3.eth.contract(address=self.token, abi=AUTHORIZATION_STATE_ABI)
        try:
            return bool(token.functions.authorizationState(to_checksum_address(payer), tx_key(nonce)).call())
        except Exception as e:  # noqa: BLE001 - RPC down or the token lacks EIP-3009: no conclusion possible
            raise SettlementPending(f"cannot read authorizationState: {type(e).__name__}") from e


class SimulatedSettlementVerifier:
    """Checks the simulated escrow's own receipts. Never evidence of a real payment."""

    simulated = True

    def __init__(self, escrow: SimulatedEscrow, token: str) -> None:
        self.escrow = escrow
        self.token = to_checksum_address(token)

    def verify_funding(self, tx_hash: str, payer: str, escrow: str, value_units: int) -> FundingEvidence:
        receipt = self.escrow.settlements.get(tx_hash)
        if receipt is None:
            raise SettlementPending("no simulated settlement with this transaction hash")
        if receipt["status"] != 1:
            raise SettlementMismatch("simulated settlement failed")
        if (receipt["from"], receipt["to"], receipt["value"]) != (to_checksum_address(payer), to_checksum_address(escrow), value_units):
            raise SettlementMismatch("simulated transfer does not match the payer, escrow and amount")
        return FundingEvidence(tx_hash, receipt["from"], receipt["to"], self.token, value_units, self.escrow.chain_id, None, 1, simulated=True)

    def authorization_used(self, payer: str, nonce: str) -> bool:
        return any(r["from"] == to_checksum_address(payer) and r["nonce"] == nonce.lower() for r in self.escrow.settlements.values())
