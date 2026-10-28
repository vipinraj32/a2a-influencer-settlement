from dataclasses import dataclass


@dataclass(frozen=True)
class TokenInfo:
    network: str
    chain_id: int
    coin: str
    contract_address: str
    decimals: int
    protocol: str  # "EIP-3009" | "Permit2"


# Testnet table from the HSP onboarding docs
TESTNET_TOKENS: tuple[TokenInfo, ...] = (
    TokenInfo("hashkey-testnet", 133, "USDC", "0x8FE3cB719Ee4410E236Cd6b72ab1fCDC06eF53c6", 6, "EIP-3009"),
    TokenInfo("hashkey-testnet", 133, "USDT", "0x372325443233fEbaC1F6998aC750276468c83CC6", 6, "Permit2"),
    TokenInfo("sepolia", 11155111, "USDC", "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238", 6, "EIP-3009"),
    TokenInfo("sepolia", 11155111, "USDT", "0xff5588b3b38dff1b4b49bfdcbf985e84d8751a0e", 6, "Permit2"),
    TokenInfo("base-sepolia", 84532, "USDC", "0x036CbD53842c5426634e7929541eC2318f3dCF7e", 6, "EIP-3009"),
)


def find_token(network: str, coin: str) -> TokenInfo:
    for t in TESTNET_TOKENS:
        if t.network == network and t.coin == coin:
            return t
    raise ValueError(f"Unsupported network/coin: {network}/{coin}")
