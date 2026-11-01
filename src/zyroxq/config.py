from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SettlementMode(StrEnum):
    # Local gateway emulator: verifies the browser-signed Payment Mandate exactly like HSP would, no chain
    SIMULATED = "simulated"
    # Real HSP order; the advertiser signs in HSP's hosted checkout (payment_url), webhook settles
    HOSTED = "hosted"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    # Storage: empty → in-memory store (tests, quick local runs)
    database_url: str = ""

    # Login: Google OAuth (influencers also grant youtube.readonly to prove their channel)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    session_secret: str = ""  # signs session cookies and encrypts influencer agent keys
    # Where the browser lands after login (the Vite dev server in development)
    frontend_url: str = ""
    # Bearer token for the stdio MCP server (read-only operator view); empty disables it
    mcp_api_token: str = ""

    # HSP
    settlement_mode: SettlementMode = SettlementMode.SIMULATED
    hsp_base_url: str = "https://merchant-qa.hashkeymerchant.com"
    hsp_app_key: str = ""
    hsp_app_secret: str = ""
    hsp_merchant_name: str = "ZyroXQ"
    hsp_merchant_private_key_path: str = "./keys/merchant_private_key.pem"
    hsp_network: str = "hashkey-testnet"
    hsp_coin: str = "USDC"
    hsp_pay_to: str = ""

    # Chain
    hashkey_testnet_rpc_url: str = "https://testnet.hsk.xyz"
    hashkey_testnet_explorer_url: str = "https://testnet-explorer.hsk.xyz"
    escrow_address: str = ""  # empty → simulated escrow ledger
    operator_private_key: str = ""  # platform key: registers deals, triggers releases
    verifier_private_key: str = ""  # signs proof attestations
    # Settlement evidence: confirmations a funding transaction needs before a deal is FUNDED
    settlement_min_confirmations: int = 2

    # Payment authorizations (EIP-3009): lifetime of a drafted authorization, and the minimum time it must
    # have left when submitted (so it cannot expire in flight)
    authorization_ttl_seconds: int = 3600
    authorization_min_remaining_seconds: int = 120
    hsp_submit_timeout_seconds: float = 30
    # Negotiation: how long one agent reply (the creator agent over A2A, or the advertiser agent's next move) may take
    # before that creator's negotiation ends as TIMED_OUT and the campaign moves on to the next creator
    negotiation_reply_timeout_seconds: float = 90

    # Verifier
    youtube_api_key: str = ""  # required to verify video proofs

    # Policy
    approval_threshold_usd: Decimal = Decimal("50")

    # Server
    public_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
