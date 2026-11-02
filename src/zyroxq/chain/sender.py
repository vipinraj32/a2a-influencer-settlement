"""One operator account sending transactions, shared by everything that writes to the chain (escrow calls, and in
sepolia mode the relayed USDC authorizations), so two senders never pick the same account nonce.

Transactions are signed locally first, so their hash is known before broadcasting: a timeout or a dropped
connection after the broadcast leaves a hash to look up, never an unknown."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from eth_account import Account

RECEIPT_TIMEOUT_SECONDS = 180


def _would_revert() -> tuple[type[Exception], ...]:
    """What a node raises when the gas estimate shows the call would revert (eth-tester, used by the tests, has its own)."""
    from web3.exceptions import ContractLogicError

    try:
        from eth_tester.exceptions import TransactionFailed
    except ImportError:
        return (ContractLogicError,)
    return ContractLogicError, TransactionFailed


WOULD_REVERT = _would_revert()


class TxReverted(Exception):
    def __init__(self, tx_hash: str, message: str = "") -> None:
        super().__init__(message or f"transaction reverted: {tx_hash}")
        self.tx_hash = tx_hash


class BroadcastUnknown(Exception):
    """Broadcasting failed (timeout, connection): the transaction may or may not be on its way. Look up `tx_hash`."""

    def __init__(self, tx_hash: str, message: str) -> None:
        super().__init__(message)
        self.tx_hash = tx_hash


class TxRejected(Exception):
    """The node refused the transaction before it was broadcast (it would revert, bad nonce, no gas): nothing was sent."""


@dataclass(frozen=True)
class SignedTx:
    tx_hash: str
    raw: bytes


@dataclass(frozen=True)
class TxResult:
    tx_hash: str
    block_number: int


def hex_hash(value: Any) -> str:
    h = value.hex() if hasattr(value, "hex") else str(value)
    return "0x" + h.removeprefix("0x")


class ChainSender:
    def __init__(self, w3: Any, private_key: str) -> None:
        self.w3 = w3
        self.account = Account.from_key(private_key)
        self.address = self.account.address
        self._lock = threading.Lock()

    def _sign(self, fn: Any) -> SignedTx:
        """Builds (the gas estimate refuses a call that would revert) and signs `fn`. Caller holds the lock."""
        try:
            tx = fn.build_transaction({"from": self.address, "nonce": self.w3.eth.get_transaction_count(self.address, "pending"),
                                       "chainId": self.w3.eth.chain_id})  # fmt: skip
        except WOULD_REVERT as e:
            raise TxRejected(f"the contract would revert: {e}") from e
        signed = self.account.sign_transaction(tx)
        return SignedTx(hex_hash(signed.hash), bytes(signed.raw_transaction))

    def submit(self, fn: Any) -> str:
        """Signs and broadcasts without waiting; returns the hash. Signing and broadcasting share one lock so two
        transactions never take the same nonce. A failed broadcast raises BroadcastUnknown carrying the hash."""
        with self._lock:
            signed = self._sign(fn)
            try:
                self.w3.eth.send_raw_transaction(signed.raw)
            except Exception as e:  # noqa: BLE001 - the node may still have it: report the hash, never guess
                raise BroadcastUnknown(signed.tx_hash, f"{type(e).__name__}: {e}") from e
        return signed.tx_hash

    def wait(self, tx_hash: str, timeout: float = RECEIPT_TIMEOUT_SECONDS) -> TxResult:
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
        if receipt["status"] != 1:
            raise TxReverted(tx_hash)
        return TxResult(tx_hash, int(receipt["blockNumber"]))

    def send(self, fn: Any) -> TxResult:
        """Submit and wait for a successful receipt. Raises TxRejected (nothing sent), TxReverted or BroadcastUnknown."""
        return self.wait(self.submit(fn))
