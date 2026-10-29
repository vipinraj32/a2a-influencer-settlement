"""Escrow backends with identical rules: `SimulatedEscrow` (in-process ledger) and `OnchainEscrow`
(CampaignEscrow.sol on HashKey Chain). Change both together with the contract.

Funding is attributed explicitly: `mark_funded(deal, funding_tx)` binds one verified settlement
transaction to one deal (never reusable), and the unallocated balance must still cover the cap."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address

ARTIFACT = Path(__file__).resolve().parents[3] / "contracts" / "build" / "CampaignEscrow.json"
# Simulated transaction hashes are 32 bytes like real ones and always start with 0x5111 ("sim")
SIMULATED_TX_PREFIX = "0x5111"


class EscrowError(Exception):
    pass


def deal_key(deal_id: str) -> bytes:
    return keccak(text=deal_id)


def tx_key(tx_hash: str) -> bytes:
    """bytes32 form of a transaction hash, as stored by CampaignEscrow.markFunded."""
    try:
        raw = bytes.fromhex(tx_hash.removeprefix("0x"))
    except ValueError:
        raw = b""
    if len(raw) != 32:
        raise EscrowError("not a 32-byte transaction hash")
    return raw


def attestation_digest(escrow: str, chain_id: int, deal_id: str, payee: str, amount_units: int, nonce: int) -> bytes:
    """Mirrors CampaignEscrow.attestationDigest."""
    return keccak(
        encode(
            ["address", "uint256", "bytes32", "address", "uint256", "uint256"],
            [to_checksum_address(escrow), chain_id, deal_key(deal_id), to_checksum_address(payee), amount_units, nonce],
        )
    )


def recover_attestation_signer(digest: bytes, signature: str) -> str:
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)


class Escrow(Protocol):
    address: str
    chain_id: int
    simulated: bool

    def register_deal(self, deal_id: str, advertiser: str, payee: str, cap_units: int, proof_deadline: int) -> str: ...
    def mark_funded(self, deal_id: str, funding_tx: str) -> str: ...
    def release(self, deal_id: str, amount_units: int, nonce: int, signature: str) -> str: ...
    def close(self, deal_id: str) -> tuple[str, int]: ...
    def refund_expired(self, deal_id: str, now: int) -> tuple[str, int]: ...
    def get_deal(self, deal_id: str) -> dict[str, Any]: ...


@dataclass
class _SimDeal:
    advertiser: str
    payee: str
    cap: int
    released: int
    proof_deadline: int
    state: str  # Registered | Funded | Disputed | Closed
    funding_tx: str = ""


class SimulatedEscrow:
    """In-process escrow ledger. SIMULATED: no token moves anywhere. `receive_transfer()` stands in for
    HSP's EIP-3009 transfer into the contract and records it the way a transaction receipt would."""

    simulated = True

    def __init__(self, verifier: str, chain_id: int = 133, address: str | None = None) -> None:
        self.address = to_checksum_address(address or "0x" + keccak(text="zyroxq-sim-escrow")[-20:].hex())
        self.chain_id = chain_id
        self.verifier = to_checksum_address(verifier)
        self.balance = 0
        self.allocated = 0
        self.transfers: list[dict[str, Any]] = []
        # Simulated settlement receipts: tx hash -> {from, to, value, status}
        self.settlements: dict[str, dict[str, Any]] = {}
        self._deals: dict[str, _SimDeal] = {}
        self._used: set[bytes] = set()
        self._used_funding: set[bytes] = set()

    @staticmethod
    def _tx() -> str:
        return SIMULATED_TX_PREFIX + secrets.token_hex(30)

    def receive_transfer(self, from_: str, value_units: int, nonce: str = "") -> str:
        """A simulated EIP-3009 transfer into the escrow; returns its simulated transaction hash."""
        tx = self._tx()
        self.balance += value_units
        self.settlements[tx] = {"from": to_checksum_address(from_), "to": self.address, "value": value_units, "nonce": nonce.lower(), "status": 1}
        return tx

    def register_deal(self, deal_id: str, advertiser: str, payee: str, cap_units: int, proof_deadline: int) -> str:
        if deal_id in self._deals:
            raise EscrowError("deal already registered")
        if cap_units <= 0:
            raise EscrowError("bad deal")
        self._deals[deal_id] = _SimDeal(advertiser, payee, cap_units, 0, proof_deadline, "Registered")
        return self._tx()

    def mark_funded(self, deal_id: str, funding_tx: str) -> str:
        d = self._require(deal_id, "Registered")
        key = tx_key(funding_tx)
        if key in self._used_funding:
            raise EscrowError("funding transaction already used")
        if self.balance - self.allocated < d.cap:
            raise EscrowError("insufficient funding")
        self._used_funding.add(key)
        self.allocated += d.cap
        d.funding_tx = funding_tx
        d.state = "Funded"
        return self._tx()

    def release(self, deal_id: str, amount_units: int, nonce: int, signature: str) -> str:
        d = self._require(deal_id, "Funded")
        if amount_units <= 0 or d.released + amount_units > d.cap:
            raise EscrowError("exceeds cap")
        digest = attestation_digest(self.address, self.chain_id, deal_id, d.payee, amount_units, nonce)
        if digest in self._used or recover_attestation_signer(digest, signature) != self.verifier:
            raise EscrowError("bad attestation")
        self._used.add(digest)
        d.released += amount_units
        self._pay(d.payee, amount_units)
        return self._tx()

    def close(self, deal_id: str) -> tuple[str, int]:
        return self._refund_remainder(deal_id)

    def refund_expired(self, deal_id: str, now: int) -> tuple[str, int]:
        d = self._require(deal_id, "Funded")
        if d.released != 0 or now <= d.proof_deadline:
            raise EscrowError("too early")
        return self._refund_remainder(deal_id)

    def get_deal(self, deal_id: str) -> dict[str, Any]:
        d = self._deals.get(deal_id)
        return {} if d is None else d.__dict__.copy()

    def _refund_remainder(self, deal_id: str) -> tuple[str, int]:
        d = self._require(deal_id, "Funded")
        remaining = d.cap - d.released
        d.state = "Closed"
        self.allocated -= remaining
        if remaining:
            self.balance -= remaining
            self.transfers.append({"to": d.advertiser, "units": remaining})
        return self._tx(), remaining

    def _pay(self, to: str, units: int) -> None:
        self.allocated -= units
        self.balance -= units
        self.transfers.append({"to": to, "units": units})

    def _require(self, deal_id: str, state: str) -> _SimDeal:
        d = self._deals.get(deal_id)
        if d is None or d.state != state:
            raise EscrowError(f"deal not in state {state}")
        return d


class OnchainEscrow:
    """CampaignEscrow.sol via web3. The operator key pays gas and sends every transaction."""

    simulated = False
    _STATES = ["None", "Registered", "Funded", "Disputed", "Closed"]

    def __init__(self, rpc_url: str, address: str, operator_private_key: str, token_address: str) -> None:
        from web3 import Web3

        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        self.address = to_checksum_address(address)
        self.contract = self.w3.eth.contract(address=self.address, abi=artifact["abi"])
        self.account = Account.from_key(operator_private_key)
        self.chain_id = self.w3.eth.chain_id
        self.token = to_checksum_address(token_address)

    def verify_deployment(self, expected_chain_id: int, verifier: str) -> None:
        """Fails closed at startup unless the RPC, the contract and the keys all match the configuration."""
        problems = []
        if self.chain_id != expected_chain_id:
            problems.append(f"RPC is chain {self.chain_id}, expected {expected_chain_id}")
        if not self.w3.eth.get_code(self.address):
            problems.append(f"no contract deployed at ESCROW_ADDRESS {self.address}")
        else:
            fns = self.contract.functions
            for name, want in (("token", self.token), ("operator", self.account.address), ("verifier", to_checksum_address(verifier))):
                got = to_checksum_address(getattr(fns, name)().call())
                if got != want:
                    problems.append(f"escrow {name}() is {got}, the configuration expects {want}")
        if problems:
            raise EscrowError("escrow deployment does not match the configuration: " + "; ".join(problems))

    def _send(self, fn: Any) -> str:
        tx = fn.build_transaction({"from": self.account.address, "nonce": self.w3.eth.get_transaction_count(self.account.address), "chainId": self.chain_id})
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise EscrowError(f"transaction reverted: {tx_hash.hex()}")
        return "0x" + tx_hash.hex().removeprefix("0x")

    def register_deal(self, deal_id: str, advertiser: str, payee: str, cap_units: int, proof_deadline: int) -> str:
        fn = self.contract.functions.registerDeal(deal_key(deal_id), to_checksum_address(advertiser), to_checksum_address(payee), cap_units, proof_deadline)
        return self._send(fn)

    def mark_funded(self, deal_id: str, funding_tx: str) -> str:
        return self._send(self.contract.functions.markFunded(deal_key(deal_id), tx_key(funding_tx)))

    def release(self, deal_id: str, amount_units: int, nonce: int, signature: str) -> str:
        return self._send(self.contract.functions.release(deal_key(deal_id), amount_units, nonce, bytes.fromhex(signature.removeprefix("0x"))))

    def close(self, deal_id: str) -> tuple[str, int]:
        remaining = self._remaining(deal_id)
        return self._send(self.contract.functions.close(deal_key(deal_id))), remaining

    def refund_expired(self, deal_id: str, now: int) -> tuple[str, int]:
        remaining = self._remaining(deal_id)
        return self._send(self.contract.functions.refundExpired(deal_key(deal_id))), remaining

    def get_deal(self, deal_id: str) -> dict[str, Any]:
        advertiser, payee, cap, released, deadline, state, funding_tx = self.contract.functions.deals(deal_key(deal_id)).call()
        return {"advertiser": advertiser, "payee": payee, "cap": cap, "released": released, "proof_deadline": deadline,
                "state": self._STATES[state], "funding_tx": "0x" + bytes(funding_tx).hex()}  # fmt: skip

    def _remaining(self, deal_id: str) -> int:
        d = self.get_deal(deal_id)
        return d["cap"] - d["released"]
