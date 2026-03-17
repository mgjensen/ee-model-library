"""
MODULE_ID:    DECOM_PROVISION_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["BESS", "WIND", "*"]

Decommissioning provision (balance sheet liability) + disposal sinking fund (cash).

Two independent components:

1. **Provision** (accounting):
   A balance-sheet liability accrued from provision_start_period to
   provision_end_period (or end of project). The provision amount sits on the
   BS as a non-cash liability; at end of life it unwinds (P&L credit).

2. **Sinking fund** (cash):
   Monthly cash contributions into a ring-fenced account to cover the actual
   disposal cost. Contributions are spread evenly from sinking_fund_start_period
   to sinking_fund_end_period. The fund is released at disposal_period.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §6
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int = Field(2025)
    start_month: int = Field(1, ge=1, le=12)

    # Master switch
    active: bool = Field(True)

    # Provision (BS liability)
    provision_amount: float = Field(0.0, ge=0, description="BS provision DKKk")
    provision_start_period: int = Field(0, ge=0)
    provision_end_period: Optional[int] = Field(
        None, description="Period when provision unwinds (default: last period)"
    )

    # Disposal cost
    disposal_cost: float = Field(0.0, ge=0, description="Actual disposal cost DKKk")
    disposal_period: Optional[int] = Field(
        None, description="Period when disposal happens (default: last period)"
    )

    # Sinking fund
    sinking_fund_active: bool = Field(False)
    sinking_fund_target: float = Field(0.0, ge=0, description="Target fund balance DKKk")
    sinking_fund_start_period: int = Field(0, ge=0)
    sinking_fund_end_period: Optional[int] = Field(
        None, description="Last contribution period (default: disposal_period - 1)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if self.provision_end_period is not None and self.provision_end_period >= n:
            raise ValueError(
                f"provision_end_period {self.provision_end_period} >= periods {n}"
            )
        if self.disposal_period is not None and self.disposal_period >= n:
            raise ValueError(
                f"disposal_period {self.disposal_period} >= periods {n}"
            )
        if self.sinking_fund_end_period is not None and self.sinking_fund_end_period >= n:
            raise ValueError(
                f"sinking_fund_end_period {self.sinking_fund_end_period} >= periods {n}"
            )
        return self


class Outputs(BaseModel):
    # Provision (BS)
    provision_balance: list[float]
    unwinding: list[float]

    # Disposal cost
    disposal_cost_monthly: list[float]

    # Sinking fund (cash)
    fund_balance: list[float]
    fund_contribution: list[float]
    fund_release: list[float]

    # Net cash
    net_cash_impact: list[float]

    # Summaries
    total_contributions: float
    total_disposal: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly decommissioning provision + sinking fund schedule."""
    n = inputs.periods

    # Inactive → all zeros
    if not inputs.active:
        z = [0.0] * n
        return Outputs(
            provision_balance=z, unwinding=z, disposal_cost_monthly=z,
            fund_balance=z, fund_contribution=z, fund_release=z,
            net_cash_impact=z,
            total_contributions=0.0, total_disposal=0.0,
        )

    prov_end = inputs.provision_end_period if inputs.provision_end_period is not None else n - 1
    disp_p = inputs.disposal_period if inputs.disposal_period is not None else n - 1

    # --- Provision ---
    provision_balance = [0.0] * n
    unwinding = [0.0] * n

    for p in range(n):
        if inputs.provision_start_period <= p <= prov_end:
            provision_balance[p] = inputs.provision_amount
        elif p > prov_end:
            provision_balance[p] = 0.0

    # Unwinding: at prov_end, the provision reverses (P&L credit)
    if inputs.provision_amount > 0 and prov_end < n:
        unwinding[prov_end] = -inputs.provision_amount

    # --- Disposal cost ---
    disposal_cost_monthly = [0.0] * n
    if inputs.disposal_cost > 0 and disp_p < n:
        disposal_cost_monthly[disp_p] = inputs.disposal_cost

    # --- Sinking fund ---
    fund_balance = [0.0] * n
    fund_contribution = [0.0] * n
    fund_release = [0.0] * n

    if inputs.sinking_fund_active and inputs.sinking_fund_target > 0:
        sf_start = inputs.sinking_fund_start_period
        sf_end = inputs.sinking_fund_end_period
        if sf_end is None:
            sf_end = disp_p - 1 if disp_p > 0 else 0

        # Number of contribution periods
        num_contrib = max(1, sf_end - sf_start + 1)
        monthly_contrib = inputs.sinking_fund_target / num_contrib

        bal = 0.0
        for p in range(n):
            # Contribution
            if sf_start <= p <= sf_end:
                fund_contribution[p] = monthly_contrib
                bal += monthly_contrib

            # Release at disposal
            if p == disp_p and bal > 0:
                fund_release[p] = bal
                bal = 0.0

            fund_balance[p] = bal

    # Net cash impact: contributions out, release in, disposal out
    net_cash = [
        -fund_contribution[p] + fund_release[p] - disposal_cost_monthly[p]
        for p in range(n)
    ]

    return Outputs(
        provision_balance=provision_balance,
        unwinding=unwinding,
        disposal_cost_monthly=disposal_cost_monthly,
        fund_balance=fund_balance,
        fund_contribution=fund_contribution,
        fund_release=fund_release,
        net_cash_impact=net_cash,
        total_contributions=sum(fund_contribution),
        total_disposal=sum(disposal_cost_monthly),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "provision_balance": (
            f"=IF(AND({r['period']}>={r['prov_start']},"
            f"{r['period']}<={r['prov_end']}),{r['provision_amount']},0)"
        ),
        "unwinding": f"=IF({r['period']}={r['prov_end']},-{r['provision_amount']},0)",
        "disposal_cost": f"=IF({r['period']}={r['disposal_period']},{r['disposal_cost']},0)",
        "fund_contribution": (
            f"=IF(AND({r['period']}>={r['sf_start']},"
            f"{r['period']}<={r['sf_end']}),{r['sf_target']}/{r['num_contrib']},0)"
        ),
        "fund_release": f"=IF({r['period']}={r['disposal_period']},{r['fund_balance']},0)",
        "net_cash_impact": f"=-{r['fund_contribution']}+{r['fund_release']}-{r['disposal_cost']}",
    }
