"""Compiles contracts/CampaignEscrow.sol → contracts/build/CampaignEscrow.json (ABI + bytecode).

Usage: uv run python scripts/compile_contracts.py
"""

import json
from pathlib import Path

import solcx

SOLC_VERSION = "0.8.24"
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "contracts" / "CampaignEscrow.sol"
OUT = ROOT / "contracts" / "build" / "CampaignEscrow.json"


def main() -> None:
    solcx.install_solc(SOLC_VERSION)
    compiled = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {SOURCE.name: {"content": SOURCE.read_text(encoding="utf-8")}},
            "settings": {
                "optimizer": {"enabled": True, "runs": 200},
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
            },
        },
        solc_version=SOLC_VERSION,
    )
    contract = compiled["contracts"][SOURCE.name]["CampaignEscrow"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    artifact = {"abi": contract["abi"], "bytecode": "0x" + contract["evm"]["bytecode"]["object"], "solc": SOLC_VERSION}
    OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(contract['abi'])} ABI entries)")


if __name__ == "__main__":
    main()
