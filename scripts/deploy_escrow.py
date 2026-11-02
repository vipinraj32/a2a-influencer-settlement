"""Deploys CampaignEscrow to HashKey Chain Testnet, or to Ethereum Sepolia with --network sepolia.

Needs in .env: OPERATOR_PRIVATE_KEY (funded with testnet gas: HSK, or Sepolia ETH), VERIFIER_PRIVATE_KEY,
and SEPOLIA_RPC_URL for Sepolia. Optional: ARBITER_ADDRESS (defaults to the operator); on HashKey,
HSP_NETWORK / HSP_COIN (default hashkey-testnet / USDC). The escrow accepts only that token, fixed at deployment;
on Sepolia it is Circle's test USDC from hsp/networks.py, checked on chain before anything is sent.
Usage: uv run python scripts/deploy_escrow.py [--network sepolia]   → prints ESCROW_ADDRESS to put in .env
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

from zyroxq.chain.settlement import verify_token
from zyroxq.hsp.networks import find_token

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv(ROOT / ".env")
    sepolia = sys.argv[1:] == ["--network", "sepolia"]
    if sys.argv[1:] and not sepolia:
        sys.exit("usage: deploy_escrow.py [--network sepolia]")
    if sepolia:
        rpc = os.environ.get("SEPOLIA_RPC_URL") or sys.exit("SEPOLIA_RPC_URL is not set")
        token, gas = find_token("sepolia", "USDC"), "ETH"
    else:
        rpc = os.environ.get("HASHKEY_TESTNET_RPC_URL", "https://testnet.hsk.xyz")
        token, gas = find_token(os.environ.get("HSP_NETWORK", "hashkey-testnet"), os.environ.get("HSP_COIN", "USDC")), "HSK"
    operator_key = os.environ.get("OPERATOR_PRIVATE_KEY") or sys.exit("OPERATOR_PRIVATE_KEY is not set")
    verifier_key = os.environ.get("VERIFIER_PRIVATE_KEY") or sys.exit("VERIFIER_PRIVATE_KEY is not set")
    operator = Account.from_key(operator_key)
    verifier = Account.from_key(verifier_key).address
    arbiter = os.environ.get("ARBITER_ADDRESS") or operator.address

    artifact = json.loads((ROOT / "contracts" / "build" / "CampaignEscrow.json").read_text(encoding="utf-8"))
    w3 = Web3(Web3.HTTPProvider(rpc))
    chain_id = w3.eth.chain_id
    if chain_id != token.chain_id:
        sys.exit(f"RPC is chain {chain_id} but {token.coin} on {token.network} is chain {token.chain_id}")
    if sepolia:
        verify_token(w3, token)  # the documented test USDC with its exact EIP-712 domain, or nothing is deployed
    print(f"chain {chain_id} · operator {operator.address} · balance {w3.from_wei(w3.eth.get_balance(operator.address), 'ether')} {gas}")
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    args = (operator.address, verifier, Web3.to_checksum_address(arbiter), Web3.to_checksum_address(token.contract_address))
    tx = contract.constructor(*args).build_transaction({"from": operator.address, "nonce": w3.eth.get_transaction_count(operator.address), "chainId": chain_id})
    tx_hash = w3.eth.send_raw_transaction(operator.sign_transaction(tx).raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        sys.exit(f"deployment reverted: {tx_hash.hex()}")
    print(f"ESCROW_ADDRESS={receipt['contractAddress']}")
    print(f"verifier {verifier} · arbiter {arbiter} · token {token.coin} {token.contract_address} · tx {tx_hash.hex()}")


if __name__ == "__main__":
    main()
