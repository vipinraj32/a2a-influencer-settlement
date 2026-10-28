from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SettlementMode(StrEnum):
    # Local gateway emulator: verifies the signed Payment Mandate exactly like HSP would, no chain
    SIMULATED = "simulated"
    # Real HSP order; the advertiser signs in HSP's hosted checkout (payment_url), webhook settles
    HOSTED = "hosted"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    # Storage: empty → in-memory store (tests / quick demo)
    database_url: str = ""

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
    escrow_address: str = ""  # empty → simulated escrow ledger
    operator_private_key: str = ""  # platform key: registers deals, triggers releases
    advertiser_private_key: str = ""  # payer wallet for the Payment Mandate
    verifier_private_key: str = ""  # signs proof attestations

    # Verifier
    youtube_api_key: str = ""  # empty → demo fixtures

    # Policy
    approval_threshold_usd: Decimal = Decimal("50")

    # Server
    public_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
