"""Independent settlement evidence. A webhook, an HTTP 200 or a browser callback only *claims* a payment
happened; a deal is FUNDED only after the settlement transaction itself proves an exact transfer of the
right token from the payer into the escrow.

`OnchainSettlementVerifier` reads the transaction receipt from HashKey Chain. `SimulatedSettlementVerifier`
reads the receipts `SimulatedEscrow` records, and every result it returns is marked simulated.

`verify_token` checks, at startup, that the configured token is what the configuration says it is."""

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


class SettlementReverted(SettlementMismatch):
    """The transaction failed on chain: it moved no tokens."""


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

    def __init__(self, rpc_url: str, token: str, chain_id: int, min_confirmations: int = 1, *, w3: Any = None) -> None:
        from web3 import Web3

        self.w3 = w3 or Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
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
            raise SettlementReverted("settlement transaction reverted")
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


TOKEN_ABI = [
    *AUTHORIZATION_STATE_ABI,
    *({"name": n, "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"name": "", "type": t}]}
      for n, t in (("name", "string"), ("version", "string"), ("decimals", "uint8"), ("DOMAIN_SEPARATOR", "bytes32"))),
]  # fmt: skip


def verify_token(w3: Any, token: Any) -> None:
    """Fails closed unless the contract at `token.contract_address` is an EIP-3009 token on the expected chain with
    the expected decimals and EIP-712 domain: a look-alike token, or a wrong domain that would make every payment
    signature invalid, stops the server at startup instead of at the first payment."""
    from eth_abi import encode

    problems: list[str] = []
    if w3.eth.chain_id != token.chain_id:
        problems.append(f"RPC is chain {w3.eth.chain_id}, expected {token.chain_id}")
    elif not w3.eth.get_code(token.contract_address):
        problems.append(f"no contract at {token.contract_address}")
    else:
        c = w3.eth.contract(address=to_checksum_address(token.contract_address), abi=TOKEN_ABI)
        try:
            decimals = c.functions.decimals().call()
            name, version = c.functions.name().call(), c.functions.version().call()
            separator = bytes(c.functions.DOMAIN_SEPARATOR().call())
            c.functions.authorizationState(to_checksum_address(token.contract_address), b"\0" * 32).call()
        except Exception as e:  # noqa: BLE001 - any missing function means it is not the token we expect
            problems.append(f"not an EIP-3009 token ({type(e).__name__})")
        else:
            if decimals != token.decimals:
                problems.append(f"decimals() is {decimals}, expected {token.decimals}")
            if (name, version) != (token.domain_name, token.domain_version):
                problems.append(f"EIP-712 domain is {name!r} v{version}, expected {token.domain_name!r} v{token.domain_version}")
            domain_type = keccak(text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
            mine = keccak(encode(["bytes32", "bytes32", "bytes32", "uint256", "address"],
                                 [domain_type, keccak(text=token.domain_name), keccak(text=token.domain_version), token.chain_id,
                                  to_checksum_address(token.contract_address)]))  # fmt: skip
            if separator != mine:
                problems.append("DOMAIN_SEPARATOR does not match the configured domain")
    if problems:
        raise SettlementMismatch(f"{token.coin} on {token.network} does not match the configuration: " + "; ".join(problems))
