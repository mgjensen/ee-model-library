"""
MODULE_ID:    DSRA_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Debt Service Reserve Account / Facility (DSRA / DSRF).

Two modes:
  - DSRA (cash reserve): actual cash set aside in a reserve account.
    Target = N months of forward-looking debt service.
    Shortfalls are funded from cash; surpluses released.
  - DSRF (letter-of-credit facility): commitment facility that replaces cash.
    No actual cash reserved; instead an annual commitment fee + arrangement fee.

Outputs:
  - Monthly target balance, actual balance, top-up, release
  - For DSRF: commitment fee, arrangement fee
  - DSRA adequacy flag (is reserve fully funded?)

Source: Holsted Hybrid vendor model §Debt Cockpit / rows 672-680.
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

_MODES = ("cash", "facility")


class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    mode: str = Field(
        "cash",
        description="'cash' = DSRA (actual cash reserve), 'facility' = DSRF (letter of credit)"
    )
    target_months: int = Field(
        6,
        ge=1, le=24,
        description="Forward-looking debt service months to cover (typically 6)"
    )

    # Monthly debt service from DEBT_001 (interest + principal)
    debt_service: list[float] = Field(
        ..., description="Monthly debt service DKKk from DEBT_001.debt_service"
    )

    # Repayment period bounds (for calculating forward-looking target)
    repayment_start_period: int = Field(
        0, ge=0,
        description="0-based period when debt repayment begins"
    )
    repayment_end_period: Optional[int] = Field(
        None,
        description="0-based period when debt is fully repaid (None = end of model)"
    )

    # DSRF-specific
    commitment_fee_rate: float = Field(
        0.0075,
        description="Annual commitment fee on DSRF facility (default 0.75%)"
    )
    arrangement_fee_rate: float = Field(
        0.0, description="One-time arrangement fee on DSRF facility"
    )
    dsrf_tenor_years: int = Field(
        10, ge=1,
        description="DSRF facility tenor in years (fee period)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.debt_service) != n:
            raise ValueError(
                f"debt_service length {len(self.debt_service)} != periods {n}"
            )
        if self.mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES}, got {self.mode!r}")
        if self.repayment_end_period is not None:
            if self.repayment_end_period <= self.repayment_start_period:
                raise ValueError("repayment_end_period must be > repayment_start_period")
        return self


class Outputs(BaseModel):
    # Monthly time-series (length = periods)
    target_balance: list[float]     # Required reserve balance DKKk
    actual_balance: list[float]     # Actual reserve balance DKKk (DSRA mode)
    top_up: list[float]             # Cash into reserve DKKk (positive = cash out)
    release: list[float]            # Cash released from reserve DKKk (positive = cash in)
    commitment_fee: list[float]     # DSRF commitment fee DKKk (0 for DSRA mode)
    arrangement_fee: list[float]    # DSRF arrangement fee DKKk (0 for DSRA mode)
    net_cash_impact: list[float]    # Net cash effect: -(top_up) + release - fees

    # Summary
    max_target: float               # Peak reserve requirement DKKk
    fully_funded: bool              # True if actual_balance >= target_balance in all periods
    total_commitment_fees: float    # Lifetime commitment fees DKKk
    total_arrangement_fees: float   # Lifetime arrangement fees DKKk


# ============================================================================
# HELPERS
# ============================================================================

def _forward_debt_service(
    debt_service: list[float],
    period: int,
    target_months: int,
    rep_end: int,
) -> float:
    """Sum of next `target_months` of debt service from `period` (inclusive)."""
    end = min(period + target_months, rep_end)
    return sum(debt_service[p] for p in range(period, end))


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """
    Compute DSRA (cash) or DSRF (facility) schedule.

    DSRA cash mode:
      - Target[p] = sum of next N months of debt service
      - Top-up[p] = max(0, target - prev_balance)
      - Release[p] = max(0, prev_balance - target)
      - Balance[p] = prev_balance + top_up - release

    DSRF facility mode:
      - Target[p] = same forward-looking calc
      - No actual cash; commitment_fee = target × annual_rate / 12
      - Arrangement fee at period 0
    """
    n = inputs.periods
    rep_start = inputs.repayment_start_period
    rep_end = inputs.repayment_end_period if inputs.repayment_end_period else n

    # Compute target balance per period
    target = [0.0] * n
    for p in range(n):
        if rep_start <= p < rep_end:
            target[p] = _forward_debt_service(
                inputs.debt_service, p, inputs.target_months, rep_end
            )
        else:
            target[p] = 0.0

    if inputs.mode == "facility":
        # DSRF — no actual cash, just fees
        commitment = [0.0] * n
        arrangement = [0.0] * n
        dsrf_end = min(rep_start + inputs.dsrf_tenor_years * 12, n)

        for p in range(rep_start, dsrf_end):
            commitment[p] = target[p] * inputs.commitment_fee_rate / 12.0

        if rep_start < n:
            arrangement[rep_start] = target[rep_start] * inputs.arrangement_fee_rate

        net_cash = [-(commitment[p] + arrangement[p]) for p in range(n)]

        return Outputs(
            target_balance=target,
            actual_balance=[0.0] * n,
            top_up=[0.0] * n,
            release=[0.0] * n,
            commitment_fee=commitment,
            arrangement_fee=arrangement,
            net_cash_impact=net_cash,
            max_target=max(target) if target else 0.0,
            fully_funded=True,  # DSRF is always "funded" by definition
            total_commitment_fees=sum(commitment),
            total_arrangement_fees=sum(arrangement),
        )

    # DSRA cash mode
    actual = [0.0] * n
    top_up = [0.0] * n
    release = [0.0] * n
    fully_funded = True

    prev_bal = 0.0
    for p in range(n):
        if target[p] > prev_bal + 1e-6:
            top_up[p] = target[p] - prev_bal
        elif prev_bal > target[p] + 1e-6:
            release[p] = prev_bal - target[p]

        actual[p] = prev_bal + top_up[p] - release[p]
        if actual[p] < target[p] - 0.01:
            fully_funded = False
        prev_bal = actual[p]

    net_cash = [-(top_up[p]) + release[p] for p in range(n)]

    return Outputs(
        target_balance=target,
        actual_balance=actual,
        top_up=top_up,
        release=release,
        commitment_fee=[0.0] * n,
        arrangement_fee=[0.0] * n,
        net_cash_impact=net_cash,
        max_target=max(target) if target else 0.0,
        fully_funded=fully_funded,
        total_commitment_fees=0.0,
        total_arrangement_fees=0.0,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "target_balance": (
            f"=SUMIFS({r['ds_range']},{r['period_range']},"
            f"\">=\"&{r['period_ref']},{r['period_range']},"
            f"\"<\"&({r['period_ref']}+{r['target_months']}))"
        ),
        "top_up": f"=MAX(0,{r['target']}-{r['prev_actual']})",
        "release": f"=MAX(0,{r['prev_actual']}-{r['target']})",
        "actual_balance": f"={r['prev_actual']}+{r['top_up']}-{r['release']}",
        "commitment_fee": f"={r['target']}*{r['commitment_rate']}/12",
    }
