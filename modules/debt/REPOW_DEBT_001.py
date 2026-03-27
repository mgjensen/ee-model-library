"""
MODULE_ID:    REPOW_DEBT_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["BESS", "*"]
CREATED:      2026-03-18
MODIFIED:     2026-03-18

Straight-line debt facility for BESS repowering.

Computes:
  - Single drawdown at repowering period (facility = LTV × repowering cost)
  - Grace period before repayment begins
  - Straight-line principal over tenor
  - Interest on opening balance with blended hedged/unhedged rate
  - Arrangement fee at drawdown

Source: EE_MODEL_BUILD_SPEC.md — Repowering facility (medium priority backlog)
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    active: bool = Field(False, description="Enable/disable the facility")
    ltv_pct: float = Field(0.0, ge=0, le=1.0, description="LTV as % of repowering cost")
    repowering_cost_DKKk: float = Field(0.0, ge=0, description="Repowering cost DKKk")
    repowering_period: int = Field(0, ge=0, description="0-based period of repowering drawdown")
    grace_period_months: int = Field(0, ge=0, description="Months after drawdown before repayment starts")
    tenor_months: int = Field(120, gt=0, description="Repayment period length in months")
    base_rate: float = Field(0.0, ge=0, description="Annual base/reference rate (floating portion)")
    margin: float = Field(0.016, description="Annual margin on top of rate")
    hedge_pct: float = Field(0.0, ge=0, le=1.0, description="Fraction of debt hedged via swap")
    swap_rate: float = Field(0.0, ge=0, description="Annual swap rate (hedged portion)")
    arrangement_fee_pct: float = Field(0.0, ge=0, description="One-off fee as % of facility at drawdown")

    @model_validator(mode="after")
    def _validate(self):
        if self.repowering_period >= self.periods:
            raise ValueError(
                f"repowering_period {self.repowering_period} >= periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]
    drawdown: list[float]
    principal: list[float]
    interest: list[float]
    closing_balance: list[float]
    debt_service: list[float]
    arrangement_fee: list[float]

    # Scalars
    total_principal: float
    total_interest: float
    facility_DKKk: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Build monthly repowering debt schedule."""
    n = inputs.periods
    zeros = lambda: [0.0] * n

    # Inactive or zero cost → all zeros
    if not inputs.active or inputs.repowering_cost_DKKk <= 0:
        return Outputs(
            opening_balance=zeros(),
            drawdown=zeros(),
            principal=zeros(),
            interest=zeros(),
            closing_balance=zeros(),
            debt_service=zeros(),
            arrangement_fee=zeros(),
            total_principal=0.0,
            total_interest=0.0,
            facility_DKKk=0.0,
        )

    facility = inputs.ltv_pct * inputs.repowering_cost_DKKk
    rp = inputs.repowering_period
    rep_start = rp + inputs.grace_period_months

    # Blended annual rate
    blended_annual = (
        inputs.hedge_pct * (inputs.swap_rate + inputs.margin)
        + (1.0 - inputs.hedge_pct) * (inputs.base_rate + inputs.margin)
    )
    monthly_rate = blended_annual / 12.0

    # Straight-line monthly principal
    sl_principal = facility / inputs.tenor_months if inputs.tenor_months > 0 else 0.0

    opening = zeros()
    draw = zeros()
    princ = zeros()
    interest = zeros()
    closing = zeros()
    ds = zeros()
    arr_fee = zeros()

    # Single drawdown at repowering period
    draw[rp] = facility

    # Arrangement fee at drawdown
    arr_fee[rp] = facility * inputs.arrangement_fee_pct

    rep_end = rep_start + inputs.tenor_months

    balance = 0.0
    for p in range(n):
        opening[p] = balance
        interest[p] = balance * monthly_rate

        if rep_start <= p < rep_end and balance > 1e-9:
            princ[p] = min(sl_principal, balance)
        else:
            princ[p] = 0.0

        closing[p] = balance + draw[p] - princ[p]
        balance = closing[p]
        ds[p] = interest[p] + princ[p]

    return Outputs(
        opening_balance=opening,
        drawdown=draw,
        principal=princ,
        interest=interest,
        closing_balance=closing,
        debt_service=ds,
        arrangement_fee=arr_fee,
        total_principal=sum(princ),
        total_interest=sum(interest),
        facility_DKKk=facility,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows."""
    r = refs
    return {
        "opening_balance": f"={r['closing_prev']}",
        "interest": f"={r['opening_bal']}*{r['monthly_rate']}/12",
        "principal": f"={r['sl_principal_fixed']}",
        "closing_balance": f"={r['opening_bal']}+{r['drawdown']}-{r['principal']}",
        "debt_service": f"={r['interest']}+{r['principal']}",
        "arrangement_fee": f"={r['facility']}*{r['arr_fee_pct']}",
    }
