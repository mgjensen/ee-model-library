"""
MODULE_ID:    OPEX_002
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "*"]
TECHNOLOGIES: ["BESS"]

BESS OPEX calculation — 4 sub-components per EE_MODEL_BUILD_SPEC.md §6.2.

Components:
    1. O&M               — fixed annual DKKk, inflation-indexed
    2. Insurance         — fixed annual DKKk, inflation-indexed
    3. Trading costs     — variable DKK/MWh discharged, inflation-indexed
    4. Other             — fixed annual DKKk, inflation-indexed

Standard indexation pattern (shared with OPEX_001):
    indexation_factor(y) = start_factor × (1 + inflation_rate)^max(0, y − inflation_start_year)

Source: EE_MODEL_BUILD_SPEC.md v2.0 §6.2
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# SUB-MODEL
# ============================================================================

class ComponentRate(BaseModel):
    """Parameters for a single fixed-rate OPEX component."""
    annual_DKKk: float = Field(0.0, description="Base annual cost DKKk (pre-inflation)")
    inflation_start_year: int = Field(2025, description="Year from which indexation compounds")
    indexation_start_factor: float = Field(1.0, description="Pre-indexation multiplier at period 0")


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    inflation_rate: float = Field(0.025, description="Annual inflation rate for all components")

    # 1. O&M — fixed annual, indexed
    om: ComponentRate = Field(default_factory=lambda: ComponentRate(annual_DKKk=0.0))

    # 2. Insurance — fixed annual, indexed
    insurance: ComponentRate = Field(default_factory=lambda: ComponentRate(annual_DKKk=0.0))

    # 3. Trading costs — variable per MWh discharged, indexed
    trading_cost_DKK_per_MWh: float = Field(
        0.0, ge=0,
        description="Base trading cost DKK/MWh discharged (pre-inflation)"
    )
    trading_inflation_start_year: int = Field(2025)
    trading_indexation_start_factor: float = Field(1.0)
    discharge_volume_MWh: list[float] = Field(
        default_factory=list,
        description="Monthly BESS discharge volume MWh (length = periods if trading_cost > 0)"
    )

    # 4. Other — fixed annual, indexed
    other: ComponentRate = Field(default_factory=lambda: ComponentRate(annual_DKKk=0.0))

    @model_validator(mode="after")
    def _validate(self):
        if self.trading_cost_DKK_per_MWh > 0 and len(self.discharge_volume_MWh) != self.periods:
            raise ValueError(
                f"discharge_volume_MWh length {len(self.discharge_volume_MWh)} != periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Monthly component arrays (length = periods) — accrual basis, DKKk
    om: list[float]
    insurance: list[float]
    trading_costs: list[float]
    other: list[float]

    total_opex: list[float]       # sum across all components per period

    # Annual aggregation (one entry per calendar year spanned)
    annual_opex: list[float]

    # Lifetime total
    total_opex_lifetime: float


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _indexation_factor(
    year: int,
    inflation_start_year: int,
    indexation_start_factor: float,
    inflation_rate: float,
) -> float:
    years = max(0, year - inflation_start_year)
    return indexation_start_factor * (1.0 + inflation_rate) ** years


def _fixed_component(
    comp: ComponentRate,
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """Monthly accrual: annual_DKKk × indexation_factor / 12."""
    monthly = [0.0] * periods
    if comp.annual_DKKk == 0.0:
        return monthly
    for year, year_periods in year_groups.items():
        factor = _indexation_factor(
            year, comp.inflation_start_year, comp.indexation_start_factor, inflation_rate
        )
        monthly_amt = comp.annual_DKKk * factor / 12.0
        for p in year_periods:
            monthly[p] = monthly_amt
    return monthly


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly BESS OPEX calculation across all 4 sub-components."""
    yg = _year_groups(inputs.periods, inputs.start_year, inputs.start_month)
    ir = inputs.inflation_rate
    n = inputs.periods

    # 1. O&M
    om = _fixed_component(inputs.om, yg, n, ir)

    # 2. Insurance
    insurance = _fixed_component(inputs.insurance, yg, n, ir)

    # 3. Trading costs — DKK/MWh × monthly discharge volume / 1000 → DKKk
    if inputs.trading_cost_DKK_per_MWh > 0:
        trading: list[float] = []
        for p in range(n):
            year = inputs.start_year + (inputs.start_month - 1 + p) // 12
            factor = _indexation_factor(
                year,
                inputs.trading_inflation_start_year,
                inputs.trading_indexation_start_factor,
                ir,
            )
            cost = (
                inputs.discharge_volume_MWh[p]
                * inputs.trading_cost_DKK_per_MWh
                * factor
                / 1000.0
            )
            trading.append(cost)
    else:
        trading = [0.0] * n

    # 4. Other
    other = _fixed_component(inputs.other, yg, n, ir)

    # Total OPEX per period
    total = [
        om[p] + insurance[p] + trading[p] + other[p]
        for p in range(n)
    ]

    # Annual aggregation
    annual = [sum(total[p] for p in plist) for plist in yg.values()]

    return Outputs(
        om=om,
        insurance=insurance,
        trading_costs=trading,
        other=other,
        total_opex=total,
        annual_opex=annual,
        total_opex_lifetime=sum(total),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key OPEX_002 rows.
    refs keys: start_factor, inflation_rate, year, inf_start_year,
               annual_base, indexation_factor,
               trading_rate, discharge_vol, om, other
    """
    r = refs
    return {
        "indexation_factor": (
            f"={r['start_factor']}*(1+{r['inflation_rate']})^MAX(0,{r['year']}-{r['inf_start_year']})"
        ),
        "fixed_component_monthly": (
            f"={r['annual_base']}*{r['indexation_factor']}/12"
        ),
        "trading_costs": (
            f"={r['discharge_vol']}*{r['trading_rate']}*{r['indexation_factor']}/1000"
        ),
        "total_opex": (
            f"=SUM({r['om']}:{r['other']})"
        ),
    }
