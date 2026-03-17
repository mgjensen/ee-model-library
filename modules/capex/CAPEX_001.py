"""
MODULE_ID:    CAPEX_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Capital expenditure schedule — 6 sub-components per EE_MODEL_BUILD_SPEC.md §7.

Components:
    1. EPC             — engineering, procurement & construction (turnkey)
    2. Grid connection — substation, cable, grid upgrade costs
    3. Development     — permitting, studies, development fees
    4. Land            — land purchase or long-term lease capitalised
    5. Contingency     — reserve applied as % of (EPC + grid + dev + land)
    6. Other           — owner's costs, insurance during construction, etc.

Each cost item is drawn down across the construction period using a monthly
drawdown profile (list of fractions summing to 1.0). The construction window
is defined by `construction_start_period` and `construction_periods`; all
spend occurs within that window.

Outputs include monthly cashflows per category, cumulative total, and the
total project cost (EPC + grid + dev + land + contingency + other).

Source: EE_MODEL_BUILD_SPEC.md v2.0 §7
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0, description="Total project life in months")

    # Construction window (0-based period indices)
    construction_start_period: int = Field(0, ge=0, description="First period of construction")
    construction_periods: int = Field(..., gt=0, description="Number of construction months")

    # Drawdown profile — fractions per construction month (must sum to ≈ 1.0)
    drawdown_profile: list[float] = Field(
        ..., description="Monthly spend fractions across construction window (sum ≈ 1.0)"
    )

    # Cost components (DKKk, nominal, undiscounted)
    epc_DKKk: float = Field(0.0, ge=0, description="EPC / turnkey construction cost DKKk")
    grid_connection_DKKk: float = Field(0.0, ge=0, description="Grid connection cost DKKk")
    development_DKKk: float = Field(0.0, ge=0, description="Development & permitting cost DKKk")
    land_DKKk: float = Field(0.0, ge=0, description="Land acquisition / capitalised lease DKKk")
    contingency_pct: float = Field(
        0.0, ge=0, le=1.0,
        description="Contingency as fraction of (EPC + grid + dev + land)"
    )
    other_DKKk: float = Field(0.0, ge=0, description="Owner's costs & other DKKk")

    # BESS repowering (mid-life battery replacement)
    repowering_cost_DKKk: float = Field(0.0, ge=0, description="BESS repowering cost DKKk")
    repowering_period: int = Field(0, ge=0, description="0-based period when repowering starts")
    repowering_drawdown_months: int = Field(1, ge=1, description="Months to spread repowering cost")

    @model_validator(mode="after")
    def _validate(self):
        if len(self.drawdown_profile) != self.construction_periods:
            raise ValueError(
                f"drawdown_profile length {len(self.drawdown_profile)} "
                f"!= construction_periods {self.construction_periods}"
            )
        total = sum(self.drawdown_profile)
        if not (0.999 <= total <= 1.001):
            raise ValueError(
                f"drawdown_profile must sum to 1.0, got {total:.6f}"
            )
        end = self.construction_start_period + self.construction_periods
        if end > self.periods:
            raise ValueError(
                f"Construction window [{self.construction_start_period}, {end}) "
                f"extends beyond periods={self.periods}"
            )
        if self.repowering_cost_DKKk > 0:
            rep_end = self.repowering_period + self.repowering_drawdown_months
            if rep_end > self.periods:
                raise ValueError(
                    f"Repowering window [{self.repowering_period}, {rep_end}) "
                    f"extends beyond periods={self.periods}"
                )
        return self


class Outputs(BaseModel):
    # Monthly spend per category (DKKk) — length = periods
    epc: list[float]
    grid_connection: list[float]
    development: list[float]
    land: list[float]
    contingency: list[float]
    other: list[float]

    # Monthly total capex (sum of all categories)
    total_capex_monthly: list[float]

    # Cumulative capex to end of each period
    cumulative_capex: list[float]

    # BESS repowering (monthly spend, length = periods)
    repowering: list[float]

    # Scalar totals
    total_epc: float
    total_grid_connection: float
    total_development: float
    total_land: float
    total_contingency: float
    total_other: float
    total_repowering: float
    total_capex: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Distribute capital costs across the construction window."""
    n = inputs.periods
    start = inputs.construction_start_period
    profile = inputs.drawdown_profile

    # Derived cost totals
    base_cost = (
        inputs.epc_DKKk
        + inputs.grid_connection_DKKk
        + inputs.development_DKKk
        + inputs.land_DKKk
    )
    contingency_total = base_cost * inputs.contingency_pct

    def _spread(total_DKKk: float) -> list[float]:
        """Distribute a total cost across construction periods via profile."""
        arr = [0.0] * n
        for i, frac in enumerate(profile):
            arr[start + i] = total_DKKk * frac
        return arr

    epc = _spread(inputs.epc_DKKk)
    grid = _spread(inputs.grid_connection_DKKk)
    dev = _spread(inputs.development_DKKk)
    land = _spread(inputs.land_DKKk)
    contingency = _spread(contingency_total)
    other = _spread(inputs.other_DKKk)

    # BESS repowering — spread evenly across repowering window
    repowering = [0.0] * n
    if inputs.repowering_cost_DKKk > 0:
        monthly_rep = inputs.repowering_cost_DKKk / inputs.repowering_drawdown_months
        for i in range(inputs.repowering_drawdown_months):
            repowering[inputs.repowering_period + i] = monthly_rep

    total_monthly = [
        epc[p] + grid[p] + dev[p] + land[p] + contingency[p] + other[p]
        + repowering[p]
        for p in range(n)
    ]

    cumulative = []
    running = 0.0
    for v in total_monthly:
        running += v
        cumulative.append(running)

    total_capex = sum(total_monthly)

    return Outputs(
        epc=epc,
        grid_connection=grid,
        development=dev,
        land=land,
        contingency=contingency,
        other=other,
        repowering=repowering,
        total_capex_monthly=total_monthly,
        cumulative_capex=cumulative,
        total_epc=inputs.epc_DKKk,
        total_grid_connection=inputs.grid_connection_DKKk,
        total_development=inputs.development_DKKk,
        total_land=inputs.land_DKKk,
        total_contingency=contingency_total,
        total_other=inputs.other_DKKk,
        total_repowering=inputs.repowering_cost_DKKk,
        total_capex=total_capex,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns Excel formula strings for CAPEX rows.
    refs keys: total_cost, drawdown_fraction, contingency_pct,
               epc, grid, dev, land, other
    """
    r = refs
    return {
        "monthly_spend": (
            f"={r['total_cost']}*{r['drawdown_fraction']}"
        ),
        "contingency": (
            f"=({r['epc']}+{r['grid']}+{r['dev']}+{r['land']})*{r['contingency_pct']}"
            f"*{r['drawdown_fraction']}"
        ),
        "total_capex_monthly": (
            f"=SUM({r['epc']}:{r['other']})"
        ),
        "cumulative_capex": (
            f"={r['prev_cumulative']}+{r['total_capex_monthly']}"
        ),
    }
