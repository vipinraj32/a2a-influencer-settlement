"""Payable amounts for the three compensation models. Pure and versioned (models.PRICING_VERSION).

    fixed   payable = fixed fee                                   (cap = fixed fee)
    cpm     payable = min(cap, eligible_views / 1000 × CPM)
    hybrid  payable = min(cap, fixed fee + eligible_views / 1000 × CPM)

Eligible views are the YouTube viewCount read `views_measured_after_days` after publishing, for a post
that is still public and still passes its proof checks (verifier). Amounts round down to the cent. The
cap is always required: the escrow must lock the most a deal can ever pay.

Example: hybrid, $19 fixed + $2.50 CPM, 10,000 eligible views, $40 cap → 19 + 25 = 44 → capped at 40.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from ..models import PRICING_VERSION, CompensationModel

CENT = Decimal("0.01")


@dataclass(frozen=True)
class PriceBreakdown:
    model: CompensationModel
    fixed: Decimal
    performance: Decimal  # after the cap
    uncapped_total: Decimal
    total: Decimal
    capped: bool
    eligible_views: int
    version: str = PRICING_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "fixed": str(self.fixed),
            "performance": str(self.performance),
            "uncapped_total": str(self.uncapped_total),
            "total": str(self.total),
            "capped": self.capped,
            "eligible_views": self.eligible_views,
            "version": self.version,
        }


def calculate(model: CompensationModel, fixed_fee: Decimal, cpm: Decimal, eligible_views: int, cap: Decimal) -> PriceBreakdown:
    if cap <= 0 or fixed_fee < 0 or cpm < 0 or eligible_views < 0:
        raise ValueError("cap must be positive; fees and views cannot be negative")
    fixed = fixed_fee if model in ("fixed", "hybrid") else Decimal("0")
    rate = cpm if model in ("cpm", "hybrid") else Decimal("0")
    if fixed > cap:
        raise ValueError(f"fixed fee {fixed} exceeds the cap {cap}")
    variable = (Decimal(eligible_views) / Decimal(1000) * rate).quantize(CENT, rounding=ROUND_DOWN)
    uncapped = fixed + variable
    performance = min(cap - fixed, variable).quantize(CENT, rounding=ROUND_DOWN)
    return PriceBreakdown(model, fixed, performance, uncapped, fixed + performance, uncapped > cap, eligible_views)


def rules() -> dict[str, object]:
    """What /api/pricing/rules publishes, so both parties read the same formula."""
    example = calculate("hybrid", Decimal("19"), Decimal("2.5"), 10_000, Decimal("40"))
    return {
        "version": PRICING_VERSION,
        "models": {
            "fixed": "payable = fixed fee; the escrow cap equals the fixed fee",
            "cpm": "payable = min(cap, eligible views / 1000 × CPM)",
            "hybrid": "payable = min(cap, fixed fee + eligible views / 1000 × CPM)",
        },
        "eligible_views": "YouTube viewCount read N days after publishing (N is in the signed terms), only while the post "
        "is public and passes its proof checks",
        "rounding": "down to the cent",
        "unpaid_remainder": "refunded from escrow to the advertiser's wallet",
        "example": example.as_dict(),
    }
