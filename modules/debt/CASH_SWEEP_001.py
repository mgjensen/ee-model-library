"""
MODULE_ID:    CASH_SWEEP_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-18
MODIFIED:     2026-03-18

Excess cash sweep to senior debt after reserves funded, before distributions.

Computes:
  - Monthly swept amount = min(available × sweep_pct, debt_opening_balance)
  - Cash remaining after sweep
  - Cumulative swept total
  - Sweep active flag (respects start/end/suspension windows)

Source: EE_MODEL_BUILD_SPEC.md v2.0 — cash sweep mechanism
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
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # Sweep parameters
    active: bool = Field(True, description="Whether cash sweep is active")
    cash_available: list[float] = Field(
        ..., description="Monthly cash available after DSRA/MRA reserves DKKk"
    )
    sweep_pct: float = Field(
        0.50, ge=0, le=1.0,
        description="Fraction of available cash swept to debt prepayment"
    )
    sweep_start_period: int = Field(
        0, ge=0, description="0-based period when sweep begins"
    )
    sweep_end_period: Optional[int] = Field(
        None, description="0-based period when sweep ends (exclusive). None = end of model."
    )

    # Suspension window
    suspension_start_period: Optional[int] = Field(
        None, description="0-based period when sweep is suspended"
    )
    suspension_end_period: Optional[int] = Field(
        None, description="0-based period when sweep resumes (exclusive)"
    )

    # Debt balance for capping sweep
    debt_opening_balance: list[float] = Field(
        ..., description="Senior debt opening balance per period DKKk"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.cash_available) != n:
            raise ValueError(
                f"cash_available length {len(self.cash_available)} != periods {n}"
            )
        if len(self.debt_opening_balance) != n:
            raise ValueError(
                f"debt_opening_balance length {len(self.debt_opening_balance)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    cash_swept: list[float]          # DKKk — amount swept per period
    cash_after_sweep: list[float]    # DKKk — cash remaining after sweep
    cumulative_swept: list[float]    # DKKk — running total of swept amounts
    sweep_active_flag: list[float]   # 1.0 if sweep active, 0.0 otherwise

    # Scalar
    total_swept: float               # DKKk — lifetime total swept


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


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly excess cash sweep calculation."""
    n = inputs.periods
    end = inputs.sweep_end_period if inputs.sweep_end_period is not None else n

    cash_swept = [0.0] * n
    cash_after_sweep = [0.0] * n
    cumulative_swept = [0.0] * n
    sweep_active_flag = [0.0] * n

    cumulative = 0.0

    for p in range(n):
        # Determine if sweep is active this period
        active = inputs.active
        if active and (p < inputs.sweep_start_period or p >= end):
            active = False
        if active and inputs.suspension_start_period is not None and inputs.suspension_end_period is not None:
            if inputs.suspension_start_period <= p < inputs.suspension_end_period:
                active = False
        if active and inputs.debt_opening_balance[p] <= 0:
            active = False

        sweep_active_flag[p] = 1.0 if active else 0.0

        if active:
            swept = min(
                max(0.0, inputs.cash_available[p]) * inputs.sweep_pct,
                inputs.debt_opening_balance[p],
            )
        else:
            swept = 0.0

        cash_swept[p] = swept
        cash_after_sweep[p] = inputs.cash_available[p] - swept
        cumulative += swept
        cumulative_swept[p] = cumulative

    return Outputs(
        cash_swept=cash_swept,
        cash_after_sweep=cash_after_sweep,
        cumulative_swept=cumulative_swept,
        sweep_active_flag=sweep_active_flag,
        total_swept=cumulative,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key rows.
    refs keys: cash_available, sweep_pct, debt_bal, sweep_flag,
               cash_swept, cumulative_prev
    """
    r = refs
    return {
        "cash_swept": (
            f"=IF({r['sweep_flag']}=1,"
            f"MIN(MAX(0,{r['cash_available']})*{r['sweep_pct']},{r['debt_bal']}),0)"
        ),
        "cash_after_sweep": f"={r['cash_available']}-{r['cash_swept']}",
        "cumulative_swept": f"={r['cumulative_prev']}+{r['cash_swept']}",
    }
