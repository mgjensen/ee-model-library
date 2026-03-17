"""
MODULE_ID:    BRIDGE_FACILITY_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]

Short-term bridge loan with bullet repayment + optional construction loan recap.

The bridge facility is drawn at drawdown_period and repaid as a bullet at
repayment_period. Interest accrues monthly on the outstanding balance.
If the bridge remains outstanding past maturity_period, a daily extension fee
applies (approximated as 30 days per month).

An optional recap (recapitalisation loan) follows the same mechanics on a
separate facility.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9
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

    # Bridge facility
    active: bool = Field(True)
    facility_size: float = Field(..., ge=0, description="Bridge loan size DKKk")
    drawdown_period: int = Field(0, ge=0, description="Period in which bridge is drawn")
    repayment_period: int = Field(..., ge=0, description="Period in which bridge is bullet-repaid")
    margin: float = Field(0.0175, ge=0, description="Annual interest margin")
    base_rate: float = Field(0.03, ge=0, description="Annual base/reference rate")
    maturity_period: int = Field(..., ge=0, description="Contractual maturity period")

    # Extension fee
    extension_fee_daily: float = Field(0.0, ge=0, description="Daily extension fee DKKk if past maturity")

    # Optional recap facility
    recap_active: bool = Field(False)
    recap_amount: float = Field(0.0, ge=0, description="Recap facility size DKKk")
    recap_margin: float = Field(0.089, ge=0)
    recap_base_rate: float = Field(0.03, ge=0)
    recap_repayment_period: int = Field(0, ge=0, description="Period in which recap is bullet-repaid")

    @model_validator(mode="after")
    def _validate(self):
        if self.drawdown_period >= self.periods:
            raise ValueError(
                f"drawdown_period {self.drawdown_period} >= periods {self.periods}"
            )
        if self.repayment_period >= self.periods:
            raise ValueError(
                f"repayment_period {self.repayment_period} >= periods {self.periods}"
            )
        if self.repayment_period < self.drawdown_period:
            raise ValueError(
                f"repayment_period {self.repayment_period} < drawdown_period {self.drawdown_period}"
            )
        if self.recap_active and self.recap_repayment_period >= self.periods:
            raise ValueError(
                f"recap_repayment_period {self.recap_repayment_period} >= periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Bridge arrays (length = periods)
    bridge_opening: list[float]
    bridge_closing: list[float]
    bridge_drawdown: list[float]
    bridge_repayment: list[float]
    bridge_interest: list[float]

    # Extension fees
    extension_fees: list[float]

    # Recap arrays (length = periods)
    recap_opening: list[float]
    recap_closing: list[float]
    recap_interest: list[float]
    recap_repayment: list[float]

    # Totals per period
    total_short_term_interest: list[float]
    total_short_term_repayment: list[float]
    total_short_term_balance: list[float]

    # Lifetime summaries
    total_bridge_interest: float
    total_recap_interest: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly bridge + recap facility schedule."""
    n = inputs.periods

    # Inactive → all zeros
    if not inputs.active:
        z = [0.0] * n
        return Outputs(
            bridge_opening=z, bridge_closing=z, bridge_drawdown=z,
            bridge_repayment=z, bridge_interest=z, extension_fees=z,
            recap_opening=z, recap_closing=z, recap_interest=z,
            recap_repayment=z,
            total_short_term_interest=z, total_short_term_repayment=z,
            total_short_term_balance=z,
            total_bridge_interest=0.0, total_recap_interest=0.0,
        )

    monthly_rate = (inputs.base_rate + inputs.margin) / 12.0

    b_opening = [0.0] * n
    b_closing = [0.0] * n
    b_drawdown = [0.0] * n
    b_repayment = [0.0] * n
    b_interest = [0.0] * n
    ext_fees = [0.0] * n

    balance = 0.0
    for p in range(n):
        # Drawdown
        dd = inputs.facility_size if p == inputs.drawdown_period else 0.0
        b_drawdown[p] = dd
        balance += dd
        b_opening[p] = balance

        # Interest on opening balance
        b_interest[p] = balance * monthly_rate

        # Bullet repayment
        rep = balance if p == inputs.repayment_period else 0.0
        b_repayment[p] = rep
        balance -= rep

        # Extension fee: outstanding past maturity
        if p > inputs.maturity_period and balance > 0:
            ext_fees[p] = inputs.extension_fee_daily * 30.0

        b_closing[p] = balance

    # --- Recap facility ---
    recap_monthly_rate = (inputs.recap_base_rate + inputs.recap_margin) / 12.0

    r_opening = [0.0] * n
    r_closing = [0.0] * n
    r_interest = [0.0] * n
    r_repayment = [0.0] * n

    if inputs.recap_active and inputs.recap_amount > 0:
        # Recap drawn at drawdown_period (same as bridge)
        r_balance = 0.0
        for p in range(n):
            dd = inputs.recap_amount if p == inputs.drawdown_period else 0.0
            r_balance += dd
            r_opening[p] = r_balance
            r_interest[p] = r_balance * recap_monthly_rate
            rep = r_balance if p == inputs.recap_repayment_period else 0.0
            r_repayment[p] = rep
            r_balance -= rep
            r_closing[p] = r_balance

    # Totals
    total_int = [b_interest[p] + r_interest[p] + ext_fees[p] for p in range(n)]
    total_rep = [b_repayment[p] + r_repayment[p] for p in range(n)]
    total_bal = [b_closing[p] + r_closing[p] for p in range(n)]

    return Outputs(
        bridge_opening=b_opening,
        bridge_closing=b_closing,
        bridge_drawdown=b_drawdown,
        bridge_repayment=b_repayment,
        bridge_interest=b_interest,
        extension_fees=ext_fees,
        recap_opening=r_opening,
        recap_closing=r_closing,
        recap_interest=r_interest,
        recap_repayment=r_repayment,
        total_short_term_interest=total_int,
        total_short_term_repayment=total_rep,
        total_short_term_balance=total_bal,
        total_bridge_interest=sum(b_interest),
        total_recap_interest=sum(r_interest),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "bridge_opening": f"={r['prev_closing']}+{r['drawdown']}",
        "bridge_interest": f"={r['bridge_opening']}*({r['base_rate']}+{r['margin']})/12",
        "bridge_repayment": f"=IF({r['period']}={r['repayment_period']},{r['bridge_opening']},0)",
        "bridge_closing": f"={r['bridge_opening']}-{r['bridge_repayment']}",
        "extension_fees": (
            f"=IF(AND({r['period']}>{r['maturity_period']},"
            f"{r['bridge_closing']}>0),{r['ext_fee_daily']}*30,0)"
        ),
        "recap_interest": f"={r['recap_opening']}*({r['recap_base_rate']}+{r['recap_margin']})/12",
        "total_short_term_interest": (
            f"={r['bridge_interest']}+{r['recap_interest']}+{r['extension_fees']}"
        ),
    }
