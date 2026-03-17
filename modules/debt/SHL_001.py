"""
MODULE_ID:    SHL_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Shareholder loan (SHL) schedule.

Models a subordinated loan from the project sponsor, typically sized as a
percentage of total equity contributed. Two modes:

  accrued=True  (default): interest compounds on the balance (PIK).
                           No cash interest is paid; interest accrues to the
                           loan balance until repayment.
  accrued=False:           interest is paid cash each period, balance stays
                           flat until repayment.

Repayment: when a repayment_schedule is provided, principal is repaid per that
schedule. Otherwise the full balance is repaid in the final period.

Balance mechanics (accrued mode):
    opening[p]  = closing[p-1]
    interest[p] = opening[p] × monthly_margin
    closing[p]  = opening[p] + interest[p] − repayment[p]

Balance mechanics (cash-pay mode):
    opening[p]  = closing[p-1]
    interest[p] = opening[p] × monthly_margin   (paid cash)
    closing[p]  = opening[p] − repayment[p]

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0, description="Total project life in months")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # SHL sizing
    shl_pct_of_equity: float = Field(
        0.80, ge=0, le=1.0,
        description="SHL as fraction of total equity contributed (e.g. 0.80 = 80%)"
    )
    margin: float = Field(
        0.0795, ge=0,
        description="Annual SHL interest margin (e.g. 0.0795 = 7.95%)"
    )
    accrued: bool = Field(
        True,
        description="If True, interest accrues (PIK); if False, interest is paid cash"
    )

    # Equity contributed — monthly series from which SHL is sized
    equity_contributed: list[float] = Field(
        ..., description="Monthly equity contributions DKKk (length = periods)"
    )

    # Optional repayment schedule — monthly amounts (length = periods)
    # If not provided, full balance is repaid in the last period.
    repayment_schedule: Optional[list[float]] = Field(
        None, description="Monthly SHL repayment amounts DKKk (optional)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.equity_contributed) != n:
            raise ValueError(
                f"equity_contributed length {len(self.equity_contributed)} != periods {n}"
            )
        if self.repayment_schedule is not None and len(self.repayment_schedule) != n:
            raise ValueError(
                f"repayment_schedule length {len(self.repayment_schedule)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]    # DKKk
    interest: list[float]           # DKKk — accrued or cash-paid
    repayment: list[float]          # DKKk — principal repaid
    closing_balance: list[float]    # DKKk

    # Cash flows (for wiring into CF_001)
    interest_cash: list[float]      # DKKk — 0 if accrued, = interest if cash-pay
    total_cash_flow: list[float]    # DKKk — repayment + interest_cash (cash out)

    # Summary
    total_interest: float
    total_repayment: float
    final_balance: float
    initial_shl: float              # = shl_pct_of_equity × sum(equity_contributed)
    fully_repaid: bool


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


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Build the monthly SHL schedule."""
    n = inputs.periods
    r = inputs.margin / 12.0

    # SHL initial amount = pct × cumulative equity at end of drawdown
    total_equity = sum(inputs.equity_contributed)
    initial_shl = inputs.shl_pct_of_equity * total_equity

    # Build drawdown: SHL is drawn proportionally to equity contributions
    if total_equity > 1e-9:
        shl_drawdown = [
            e * inputs.shl_pct_of_equity for e in inputs.equity_contributed
        ]
    else:
        shl_drawdown = [0.0] * n

    # Repayment schedule
    if inputs.repayment_schedule is not None:
        repay_sched = list(inputs.repayment_schedule)
    else:
        # Default: full repayment in last period
        repay_sched = [0.0] * n

    opening = [0.0] * n
    interest = [0.0] * n
    repayment = [0.0] * n
    closing = [0.0] * n

    balance = 0.0
    for p in range(n):
        balance += shl_drawdown[p]
        opening[p] = balance
        interest[p] = balance * r

        if inputs.accrued:
            # PIK: interest added to balance
            balance += interest[p]
        # else: interest paid cash, balance unchanged

        # Repayment
        rep = min(repay_sched[p], balance) if repay_sched[p] > 0 else 0.0
        # Default repayment in final period if no schedule provided
        if inputs.repayment_schedule is None and p == n - 1:
            rep = balance
        repayment[p] = rep
        balance -= rep
        closing[p] = balance

    # Cash flows
    interest_cash = [0.0] * n if inputs.accrued else list(interest)
    total_cash = [repayment[p] + interest_cash[p] for p in range(n)]

    return Outputs(
        opening_balance=opening,
        interest=interest,
        repayment=repayment,
        closing_balance=closing,
        interest_cash=interest_cash,
        total_cash_flow=total_cash,
        total_interest=sum(interest),
        total_repayment=sum(repayment),
        final_balance=closing[-1] if closing else 0.0,
        initial_shl=initial_shl,
        fully_repaid=closing[-1] < 0.01 if closing else True,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "opening_balance": f"={r['closing_prev']}+{r['shl_drawdown']}",
        "interest": f"={r['opening_bal']}*{r['monthly_margin']}",
        "closing_balance_accrued": (
            f"={r['opening_bal']}+{r['interest']}-{r['repayment']}"
        ),
        "closing_balance_cash": (
            f"={r['opening_bal']}-{r['repayment']}"
        ),
    }
