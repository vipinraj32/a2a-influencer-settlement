"""Advisory token balance lookups for the signing panel. A balance can change between the check and
settlement, so it never decides anything: settlement is verified separately (settlement.py)."""

from __future__ import annotations

from typing import Protocol

from eth_utils import to_checksum_address

BALANCE_OF_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]


class BalanceReader(Protocol):
    def balance_of(self, account: str) -> int:
        """Token balance in base units (raises on RPC failure)."""
        ...


class RpcBalanceReader:
    def __init__(self, rpc_url: str, token: str) -> None:
        self.rpc_url = rpc_url
        self.token = to_checksum_address(token)
        self._contract = None

    def balance_of(self, account: str) -> int:
        if self._contract is None:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))
            self._contract = w3.eth.contract(address=self.token, abi=BALANCE_OF_ABI)
        return int(self._contract.functions.balanceOf(to_checksum_address(account)).call())
