"""
MODULE_ID:    OPEX_003
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "SE", "PL", "*"]
TECHNOLOGIES: ["WIND"]

Wind OPEX calculation — 6 sub-components per EE_MODEL_BUILD_SPEC.md §6.3.

Wind turbine operating costs differ from PV:
  - Full-scope O&M service contract covers gearbox, blades, pitch/yaw, electrical
  - Land lease is typically per-turbine or per-MW (not per-hectare like PV)
  - No inverter replacement (wind uses power converters, covered in O&M)
  - Grid costs may include fixed balancing/profile tariffs

Standard pattern per component:
    cost = annual_base × indexation_factor(year)
    accrued monthly = annual_cost / 12
    indexation_factor(y) = start_factor × (1 + inflation_rate)^max(0, y − inflation_start_year)

Source: EE_MODEL_BUILD_SPEC.md v2.0 §6.3
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
    sensitivity_factor: float = Field(1.0, description="Multiplier for scenario analysis")

    # Capacity
    capacity_mw: float = Field(..., gt=0, description="Installed wind capacity MW")

    # --- Fixed-rate components (annual DKKk total) ---
    # 1. O&M service contract (full-scope: gearbox, blades, pitch, electrical)
    om: ComponentRate

    # 2. Land lease (per turbine or per MW, captured in annual_DKKk)
    land_lease: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="Annual land lease DKKk"
    )

    # 3. Insurance (property damage + liability)
    insurance: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="Annual insurance premium DKKk"
    )

    # 4. Grid costs (fixed annual grid connection / profile tariff)
    grid_costs: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="Annual fixed grid costs DKKk"
    )

    # 5. Other (auditor, spare parts, environmental monitoring)
    other: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="Annual other costs DKKk"
    )


class Outputs(BaseModel):
    # Monthly component arrays (length = periods) — accrual basis
    om: list[float]
    land_lease: list[float]
    insurance: list[float]
    grid_costs: list[float]
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
    """Monthly accrual = annual_DKKk × indexation_factor / 12."""
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
    """Monthly wind OPEX calculation across all 5 sub-components."""
    yg = _year_groups(inputs.periods, inputs.start_year, inputs.start_month)
    ir = inputs.inflation_rate
    n = inputs.periods

    om         = _fixed_component(inputs.om,         yg, n, ir)
    land_lease = _fixed_component(inputs.land_lease, yg, n, ir)
    insurance  = _fixed_component(inputs.insurance,  yg, n, ir)
    grid_costs = _fixed_component(inputs.grid_costs, yg, n, ir)
    other      = _fixed_component(inputs.other,      yg, n, ir)

    total = [
        om[p] + land_lease[p] + insurance[p] + grid_costs[p] + other[p]
        for p in range(n)
    ]

    # Apply sensitivity factor
    if inputs.sensitivity_factor != 1.0:
        sf = inputs.sensitivity_factor
        total = [v * sf for v in total]

    annual = [sum(total[p] for p in plist) for plist in yg.values()]

    return Outputs(
        om=om,
        land_lease=land_lease,
        insurance=insurance,
        grid_costs=grid_costs,
        other=other,
        total_opex=total,
        annual_opex=annual,
        total_opex_lifetime=sum(total),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "indexation_factor": (
            f"={r['start_factor']}*(1+{r['inflation_rate']})^MAX(0,{r['year']}-{r['inf_start_year']})"
        ),
        "fixed_component_monthly": (
            f"={r['annual_base']}*{r['indexation_factor']}/12"
        ),
        "total_opex": (
            f"=SUM({r['om']}:{r['other']})"
        ),
    }
