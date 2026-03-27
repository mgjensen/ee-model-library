"""
MODULE_ID:    BREAKEVEN_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-18
MODIFIED:     2026-03-18

Breakeven price analysis.

Computes the minimum average capture price at which costs are covered,
per period and across the project lifetime / debt tenor.
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

    # Monthly time-series (wired from upstream)
    production_MWh: list[float] = Field(..., description="Monthly net production MWh")
    total_opex_monthly: list[float] = Field(..., description="Monthly OPEX DKKk")
    debt_service_monthly: list[float] = Field(
        default_factory=list, description="Monthly debt service DKKk"
    )
    tax_monthly: list[float] = Field(default_factory=list, description="Monthly tax DKKk")

    # Debt tenor for average-over-tenor calc
    debt_tenor_end_period: int = Field(0, ge=0, description="Last period of debt tenor")

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.production_MWh) != n:
            raise ValueError(f"production_MWh length {len(self.production_MWh)} != periods {n}")
        if len(self.total_opex_monthly) != n:
            raise ValueError(f"total_opex_monthly length {len(self.total_opex_monthly)} != periods {n}")
        if self.debt_service_monthly and len(self.debt_service_monthly) != n:
            raise ValueError(f"debt_service_monthly length != periods")
        if self.tax_monthly and len(self.tax_monthly) != n:
            raise ValueError(f"tax_monthly length != periods")
        return self


class Outputs(BaseModel):
    breakeven_price_monthly: list[float]    # DKK/MWh per period
    average_breakeven: float                # Volume-weighted avg over lifetime
    max_breakeven: float                    # Max monthly breakeven
    average_over_debt_tenor: float          # Volume-weighted avg over debt tenor
    average_over_lifetime: float            # Same as average_breakeven


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    ds = inputs.debt_service_monthly or [0.0] * n
    tax = inputs.tax_monthly or [0.0] * n

    be = [0.0] * n
    for p in range(n):
        total_cost = inputs.total_opex_monthly[p] + ds[p] + tax[p]
        prod = inputs.production_MWh[p]
        if prod > 1e-6:
            # Convert DKKk to DKK/MWh: cost is in DKKk, prod in MWh
            be[p] = total_cost * 1000.0 / prod  # DKK/MWh
        else:
            be[p] = 0.0

    # Volume-weighted averages
    total_cost_life = sum(inputs.total_opex_monthly[p] + ds[p] + tax[p] for p in range(n))
    total_prod_life = sum(inputs.production_MWh)
    avg_be = (total_cost_life * 1000.0 / total_prod_life) if total_prod_life > 1e-6 else 0.0

    tenor_end = min(inputs.debt_tenor_end_period, n) if inputs.debt_tenor_end_period > 0 else n
    total_cost_tenor = sum(inputs.total_opex_monthly[p] + ds[p] + tax[p] for p in range(tenor_end))
    total_prod_tenor = sum(inputs.production_MWh[p] for p in range(tenor_end))
    avg_tenor = (total_cost_tenor * 1000.0 / total_prod_tenor) if total_prod_tenor > 1e-6 else 0.0

    max_be = max(be) if be else 0.0

    return Outputs(
        breakeven_price_monthly=be,
        average_breakeven=avg_be,
        max_breakeven=max_be,
        average_over_debt_tenor=avg_tenor,
        average_over_lifetime=avg_be,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "breakeven": f"=IF({r['production']}>0,({r['opex']}+{r['ds']}+{r['tax']})*1000/{r['production']},0)",
    }
