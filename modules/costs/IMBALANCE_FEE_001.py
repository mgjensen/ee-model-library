"""
MODULE_ID:    IMBALANCE_FEE_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["LT", "*"]
TECHNOLOGIES: ["PV", "WIND", "*"]

Polluter pays imbalance fee.

Grid cost charged on the imbalance between forecast and actual production.
Lithuania-specific but applicable to other markets with balancing regimes.

Source: Tailwind valuation model — SPV sheets.
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    active: bool = Field(True)
    fee_per_MWh: float = Field(0.0, ge=0, description="Imbalance fee per MWh")
    production_MWh: list[float] = Field(..., description="Monthly net production MWh")
    imbalance_pct: float = Field(0.05, ge=0, le=1.0, description="Assumed imbalance as % of production")
    inflation_rate: float = Field(0.025)
    inflation_start_year: int = Field(2025)

    @model_validator(mode="after")
    def _validate(self):
        if len(self.production_MWh) != self.periods:
            raise ValueError(
                f"production_MWh length {len(self.production_MWh)} != periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    imbalance_fee_monthly: list[float]  # DKKk per period
    total_imbalance_fee: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    if not inputs.active or inputs.fee_per_MWh == 0.0:
        return Outputs(
            imbalance_fee_monthly=[0.0] * n,
            total_imbalance_fee=0.0,
        )

    fees = [0.0] * n
    for p in range(n):
        offset = inputs.start_month - 1 + p
        year = inputs.start_year + offset // 12
        factor = (1 + inputs.inflation_rate) ** max(0, year - inputs.inflation_start_year)
        imbalance_vol = inputs.production_MWh[p] * inputs.imbalance_pct
        fees[p] = imbalance_vol * inputs.fee_per_MWh * factor / 1000.0  # DKKk

    return Outputs(
        imbalance_fee_monthly=fees,
        total_imbalance_fee=sum(fees),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "imbalance_fee": (
            f"={r['production']}*{r['imbalance_pct']}*{r['fee_per_MWh']}"
            f"*{r['inflation_factor']}/1000"
        ),
    }
