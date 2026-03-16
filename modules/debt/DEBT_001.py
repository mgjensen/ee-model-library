"""
MODULE_ID:    DEBT_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Senior debt schedule for project finance.

Computes:
  - Monthly opening balance, drawdowns, interest, principal, closing balance
  - Debt service (interest + principal) per period
  - Annual DSCR mapped to monthly periods (when CFADS provided)
  - Covenant breach flag

Repayment types: annuity, straight_line, bullet
Interest calculated on opening balance each month (interest not capitalised).

Source: EE_MODEL_BUILD_SPEC.md v2.0 §8, §16 (Project Viuf: 450,000 DKKk, 25y, 5.0%)
"""

from __future__ import annotations

import math
from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Facility terms
    facility: float = Field(..., gt=0, description="Total debt facility DKKk")
    all_in_rate: float = Field(..., gt=0, description="Annual all-in interest rate (margin + fees)")
    repayment_type: str = Field("annuity", description="annuity | straight_line | bullet")
    tenor_months: int = Field(..., gt=0, description="Repayment period length in months")

    # Drawdown schedule — monthly amounts, length = periods, must sum to <= facility
    drawdowns: list[float] = Field(..., description="Monthly drawdown amounts DKKk")

    # Repayment start — None infers from last non-zero drawdown + 1
    repayment_start_period: Optional[int] = Field(
        None, description="0-based period index when repayment begins (None = infer)"
    )

    # Timeline
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # DSCR
    dscr_covenant: float = Field(1.10, description="Minimum DSCR covenant")
    cfads: Optional[list[float]] = Field(
        None, description="Monthly CFADS for DSCR calculation DKKk"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.drawdowns) != n:
            raise ValueError(f"drawdowns length {len(self.drawdowns)} != periods {n}")
        if abs(sum(self.drawdowns) - self.facility) > 0.01:
            raise ValueError(
                f"drawdowns sum {sum(self.drawdowns):.1f} != facility {self.facility:.1f}"
            )
        if self.repayment_type not in ("annuity", "straight_line", "bullet"):
            raise ValueError(f"Unknown repayment_type: {self.repayment_type!r}")
        if self.cfads is not None and len(self.cfads) != n:
            raise ValueError(f"cfads length {len(self.cfads)} != periods {n}")
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]    # DKKk — balance at start of period
    drawdown: list[float]           # DKKk — drawdown in period
    interest: list[float]           # DKKk — interest on opening balance
    principal: list[float]          # DKKk — principal repaid
    closing_balance: list[float]    # DKKk — balance at end of period
    debt_service: list[float]       # DKKk — interest + principal
    dscr_annual: list[float]        # Annual DSCR mapped to each period (nan if no CFADS)

    # Summary
    total_interest: float
    total_principal: float
    final_balance: float            # Should be ~0 if fully repaid within model
    min_dscr: float                 # Minimum during repayment (nan if no CFADS)
    covenant_breached: bool
    fully_repaid: bool              # True if final_balance < 0.01 DKKk


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    """Map period indices to calendar years."""
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _infer_repayment_start(drawdowns: list[float]) -> int:
    """First period after the last non-zero drawdown (or 0 if no drawdowns)."""
    last = max((i for i, d in enumerate(drawdowns) if d > 1e-9), default=-1)
    return last + 1


def _annuity_payment(balance: float, monthly_rate: float, n: int) -> float:
    """Constant annuity payment for a given balance, rate, and n periods."""
    if n <= 0:
        return balance
    if monthly_rate < 1e-12:
        return balance / n
    return balance * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -n)


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate_schedule(inputs: Inputs) -> Outputs:
    """
    Build the full monthly debt schedule.

    Balance mechanics:
      opening[p] = closing[p-1]
      interest[p] = opening[p] × monthly_rate
      closing[p]  = opening[p] + drawdown[p] − principal[p]
      (interest is paid from cash, never capitalised)
    """
    r = inputs.all_in_rate / 12.0

    rep_start = inputs.repayment_start_period
    if rep_start is None:
        rep_start = _infer_repayment_start(inputs.drawdowns)

    # Balance at repayment start = cumulative drawdowns (no capitalisation)
    balance_at_rep_start = sum(inputs.drawdowns[:rep_start])

    # Pre-compute per-period principal for straight-line and annuity
    if inputs.repayment_type == "annuity":
        annuity_pmt = _annuity_payment(balance_at_rep_start, r, inputs.tenor_months)
        sl_principal = None
    elif inputs.repayment_type == "straight_line":
        sl_principal = balance_at_rep_start / inputs.tenor_months if inputs.tenor_months else 0.0
        annuity_pmt = None
    else:  # bullet
        annuity_pmt = None
        sl_principal = None

    # Build schedule
    opening_arr = [0.0] * inputs.periods
    interest_arr = [0.0] * inputs.periods
    principal_arr = [0.0] * inputs.periods
    closing_arr = [0.0] * inputs.periods

    balance = 0.0
    repayment_count = 0  # periods since repayment started

    for p in range(inputs.periods):
        opening_arr[p] = balance
        interest_arr[p] = balance * r

        if p < rep_start or balance < 1e-9:
            princ = 0.0
        else:
            repayment_count += 1
            if inputs.repayment_type == "annuity":
                princ = max(0.0, annuity_pmt - interest_arr[p])
            elif inputs.repayment_type == "straight_line":
                princ = sl_principal
            else:  # bullet
                princ = balance if repayment_count == inputs.tenor_months else 0.0
            princ = min(princ, balance)  # never repay more than outstanding

        principal_arr[p] = princ
        closing_arr[p] = balance + inputs.drawdowns[p] - princ
        balance = closing_arr[p]

    debt_service = [interest_arr[p] + principal_arr[p] for p in range(inputs.periods)]

    # DSCR — computed annually, mapped back to monthly periods
    dscr_monthly = [float("nan")] * inputs.periods
    min_dscr = float("nan")
    covenant_breached = False

    if inputs.cfads is not None:
        yg = _year_groups(inputs.periods, inputs.start_year, inputs.start_month)
        running_min = math.inf
        for year_periods in yg.values():
            year_ds = sum(debt_service[p] for p in year_periods)
            year_principal = sum(principal_arr[p] for p in year_periods)
            if year_ds < 1e-9 or year_principal < 1e-9:
                continue  # no debt service or no principal this year — skip
            year_cfads = sum(inputs.cfads[p] for p in year_periods)
            dscr = year_cfads / year_ds
            for p in year_periods:
                dscr_monthly[p] = dscr
            if dscr < running_min:
                running_min = dscr
            if dscr < inputs.dscr_covenant:
                covenant_breached = True
        min_dscr = running_min if running_min < math.inf else float("nan")

    final_bal = closing_arr[-1] if closing_arr else 0.0

    return Outputs(
        opening_balance=opening_arr,
        drawdown=list(inputs.drawdowns),
        interest=interest_arr,
        principal=principal_arr,
        closing_balance=closing_arr,
        debt_service=debt_service,
        dscr_annual=dscr_monthly,
        total_interest=sum(interest_arr),
        total_principal=sum(principal_arr),
        final_balance=final_bal,
        min_dscr=min_dscr,
        covenant_breached=covenant_breached,
        fully_repaid=final_bal < 0.01,
    )


# Alias for consistency with other modules
def calculate(inputs: Inputs) -> Outputs:
    return calculate_schedule(inputs)


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key debt schedule rows.
    refs keys: opening_bal, drawdown, monthly_rate, annuity_pmt,
               principal, interest, debt_service, cfads
    """
    r = refs
    return {
        "opening_balance":  f"={r['closing_prev']}",
        "interest":         f"={r['opening_bal']}*{r['monthly_rate']}",
        "annuity_principal": f"=MAX(0,{r['annuity_pmt']}-{r['interest']})",
        "sl_principal":     f"={r['sl_principal_fixed']}",
        "closing_balance":  f"={r['opening_bal']}+{r['drawdown']}-{r['principal']}",
        "debt_service":     f"={r['interest']}+{r['principal']}",
        "dscr_annual":      f"=SUMIFS({r['cfads_range']},{r['year_range']},{r['year_ref']})"
                            f"/SUMIFS({r['ds_range']},{r['year_range']},{r['year_ref']})",
        "annuity_payment":  (
            f"={r['balance']}*{r['monthly_rate']}"
            f"/(1-(1+{r['monthly_rate']})^(-{r['tenor_months']}))"
        ),
    }
