"""
MODULE_ID:    DEBT_REFI_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Sculpted refinancing facility.

Models replacement of an existing debt facility at a specified operational date
with a new sculpted facility. The refi facility draws down the outstanding
balance of the original facility and establishes a new repayment profile
based on CFADS-weighted blended DSCR targets.

Supports:
  - 4-stream revenue allocation with per-stream DSCR targets
  - Margin step-ups (up to 3 periods)
  - Cash sweep on refi facility
  - Arrangement fee at drawdown
  - LLCR per-period

Source: Holsted Hybrid vendor model §2.2 C_Financing rows 300-374.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Optional
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# SUB-MODELS
# ============================================================================

class DSCRStream(BaseModel):
    """Per-revenue-stream DSCR target."""
    name: str = Field(..., description="e.g. 'pv_contracted'")
    target_dscr: float = Field(..., gt=0)
    cfads: list[float] = Field(..., description="Monthly CFADS DKKk")


class MarginStep(BaseModel):
    """One step in a margin schedule."""
    margin: float = Field(..., description="Annual margin")
    until_year: int = Field(..., description="Calendar year (inclusive) this step ends")


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    # Refi timing
    active: bool = Field(False)
    refi_period: int = Field(..., ge=0, description="Period index when refinancing occurs")
    grace_periods: int = Field(0, ge=0, description="Grace months after refi before repayment")
    tenor_months: int = Field(..., gt=0, description="Repayment tenor in months")

    # Facility
    original_balance_at_refi_DKKk: float = Field(
        ..., gt=0, description="Outstanding balance of original facility at refi date"
    )

    # Interest
    base_rate: float = Field(0.03, ge=0, description="Annual base/reference rate")
    margin_schedule: list[MarginStep] = Field(
        default_factory=list, description="Margin step-ups (chronological)"
    )
    default_margin: float = Field(0.015, ge=0, description="Margin if no schedule provided")
    swap_rate: Optional[float] = Field(None, description="Fixed swap rate for hedged portion")
    hedge_pct: float = Field(0.0, ge=0.0, le=1.0, description="Fraction of facility hedged")

    # DSCR streams
    sculpted_dscr_streams: list[DSCRStream] = Field(
        default_factory=list, description="Per-stream DSCR targets and monthly CFADS"
    )

    # Cash sweep
    cash_sweep_active: bool = Field(False)
    cash_sweep_pct: float = Field(0.0, ge=0.0, le=1.0)

    # Fees
    arrangement_fee_pct: float = Field(0.0, ge=0.0, description="Arrangement fee as % of facility")

    # Repayment frequency
    payment_months: list[int] = Field(
        default_factory=lambda: [6, 12],
        description="Calendar months when debt service is due (e.g. [6, 12])"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for i, s in enumerate(self.sculpted_dscr_streams):
            if len(s.cfads) != n:
                raise ValueError(
                    f"sculpted_dscr_streams[{i}].cfads length {len(s.cfads)} != periods {n}"
                )
        if self.margin_schedule:
            prev = -1
            for i, step in enumerate(self.margin_schedule):
                if step.until_year <= prev:
                    raise ValueError(
                        f"margin_schedule[{i}].until_year must be > previous"
                    )
                prev = step.until_year
        for m in self.payment_months:
            if m < 1 or m > 12:
                raise ValueError(f"payment_months must be 1-12, got {m}")
        return self


class Outputs(BaseModel):
    refi_facility_DKKk: float
    opening_balance: list[float]
    drawdown: list[float]
    interest: list[float]
    principal: list[float]
    cash_sweep: list[float]
    closing_balance: list[float]
    debt_service: list[float]
    arrangement_fee: list[float]
    blended_dscr: list[float]
    dscr_achieved: list[float]
    llcr: list[float]
    total_principal: float
    total_interest: float
    total_cash_sweep: float
    fully_repaid: bool


# ============================================================================
# HELPERS
# ============================================================================

def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _nans(n: int) -> list[float]:
    return [float("nan")] * n


def _empty_outputs(n: int) -> Outputs:
    return Outputs(
        refi_facility_DKKk=0.0,
        opening_balance=_zeros(n),
        drawdown=_zeros(n),
        interest=_zeros(n),
        principal=_zeros(n),
        cash_sweep=_zeros(n),
        closing_balance=_zeros(n),
        debt_service=_zeros(n),
        arrangement_fee=_zeros(n),
        blended_dscr=_nans(n),
        dscr_achieved=_nans(n),
        llcr=_nans(n),
        total_principal=0.0,
        total_interest=0.0,
        total_cash_sweep=0.0,
        fully_repaid=True,
    )


def _monthly_rate_at(
    p: int,
    inputs: Inputs,
) -> float:
    """Blended monthly rate at period p."""
    # Determine calendar year
    offset = inputs.start_month - 1 + p
    year = inputs.start_year + offset // 12

    # Find applicable margin
    margin = inputs.default_margin
    if inputs.margin_schedule:
        margin = inputs.margin_schedule[-1].margin  # default: last step
        for step in inputs.margin_schedule:
            if year <= step.until_year:
                margin = step.margin
                break

    unhedged_annual = inputs.base_rate + margin
    if inputs.swap_rate is not None and inputs.hedge_pct > 0:
        hedged_annual = inputs.swap_rate + margin
        annual = inputs.hedge_pct * hedged_annual + (1 - inputs.hedge_pct) * unhedged_annual
    else:
        annual = unhedged_annual
    return annual / 12.0


def _total_cfads(streams: list[DSCRStream], n: int) -> list[float]:
    if not streams:
        return _zeros(n)
    return [sum(s.cfads[p] for s in streams) for p in range(n)]


def _blended_dscr_for_periods(
    streams: list[DSCRStream],
    periods: list[int],
) -> float:
    """CFADS-weighted blended target DSCR for a set of periods."""
    total = 0.0
    weighted = 0.0
    for s in streams:
        scf = sum(s.cfads[p] for p in periods)
        total += scf
        weighted += scf * s.target_dscr
    return weighted / total if total > 1e-9 else 999.0


def _sa_groups(
    n: int, start_month: int, payment_months: list[int],
) -> list[list[int]]:
    """Group period indices into semi-annual (or other) payment groups."""
    groups: list[list[int]] = []
    current: list[int] = []
    pm_set = set(payment_months)
    for p in range(n):
        current.append(p)
        cal_month = ((start_month - 1 + p) % 12) + 1
        if cal_month in pm_set:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods

    if not inputs.active or inputs.refi_period >= n:
        return _empty_outputs(n)

    facility = inputs.original_balance_at_refi_DKKk
    rp = inputs.refi_period
    rep_start = rp + inputs.grace_periods
    rep_end = min(rp + inputs.grace_periods + inputs.tenor_months, n)
    streams = inputs.sculpted_dscr_streams
    cfads = _total_cfads(streams, n)

    # Group periods into SA payment groups
    sa_groups_all = _sa_groups(n, inputs.start_month, inputs.payment_months)
    pm_set = set(inputs.payment_months)

    # Build schedule
    opening = _zeros(n)
    draw = _zeros(n)
    interest_arr = _zeros(n)
    principal_arr = _zeros(n)
    closing = _zeros(n)
    blended_arr = _nans(n)
    dscr_arr = _nans(n)
    fee_arr = _zeros(n)

    draw[rp] = facility
    fee_arr[rp] = facility * inputs.arrangement_fee_pct

    # Sculpted repayment using SA groups
    balance = 0.0
    for sa_months in sa_groups_all:
        pay_p = sa_months[-1]
        is_payment_month = (((inputs.start_month - 1 + pay_p) % 12) + 1) in pm_set
        is_repayment = is_payment_month and rep_start <= pay_p < rep_end

        # Blended DSCR for this SA group
        if streams:
            bl = _blended_dscr_for_periods(streams, sa_months)
            for p in sa_months:
                blended_arr[p] = bl

        # Forward pass: accrue interest, apply drawdowns
        sa_interest = 0.0
        for p in sa_months:
            opening[p] = balance
            r = _monthly_rate_at(p, inputs)
            int_p = balance * r if p >= rp else 0.0
            interest_arr[p] = int_p
            sa_interest += int_p
            if p == rp:
                balance += facility
            closing[p] = balance

        if is_repayment and balance > 1e-9 and streams:
            sa_cfads = sum(cfads[p] for p in sa_months)
            bl = _blended_dscr_for_periods(streams, sa_months)
            max_ds = sa_cfads / bl if bl > 1e-9 else 0.0
            princ = max(0.0, max_ds - sa_interest)
            princ = min(princ, balance)
            principal_arr[pay_p] = princ
            balance -= princ
            closing[pay_p] = balance

            # Achieved DSCR
            actual_ds = sa_interest + princ
            achieved = sa_cfads / actual_ds if actual_ds > 1e-9 else float("nan")
            for p in sa_months:
                dscr_arr[p] = achieved

    # Cash sweep
    sweep = _zeros(n)
    if inputs.cash_sweep_active and inputs.cash_sweep_pct > 0:
        for p in range(rep_start, n):
            if closing[p] <= 1e-9:
                continue
            excess = cfads[p] - (interest_arr[p] + principal_arr[p])
            amt = max(0.0, excess * inputs.cash_sweep_pct)
            amt = min(amt, closing[p])
            sweep[p] = amt
            closing[p] -= amt

    ds = [interest_arr[p] + principal_arr[p] + sweep[p] for p in range(n)]
    final_bal = closing[-1] if closing else 0.0

    # LLCR per-period
    maturity = max((p for p in range(n) if closing[p] > 0.01), default=rp)
    llcr_arr = _nans(n)
    for p in range(rp, n):
        if opening[p] < 0.01:
            continue
        pv = 0.0
        discount = 1.0
        for s in range(p, maturity + 1):
            pv += cfads[s] * discount
            discount *= 1.0 / (1.0 + _monthly_rate_at(s, inputs))
        llcr_arr[p] = pv / opening[p] if opening[p] > 0.01 else float("nan")

    return Outputs(
        refi_facility_DKKk=facility,
        opening_balance=opening,
        drawdown=draw,
        interest=interest_arr,
        principal=principal_arr,
        cash_sweep=sweep,
        closing_balance=closing,
        debt_service=ds,
        arrangement_fee=fee_arr,
        blended_dscr=blended_arr,
        dscr_achieved=dscr_arr,
        llcr=llcr_arr,
        total_principal=sum(principal_arr),
        total_interest=sum(interest_arr),
        total_cash_sweep=sum(sweep),
        fully_repaid=final_bal < 0.01,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "drawdown": f"=IF({r['period']}={r['refi_period']},{r['original_balance']},0)",
        "interest": f"={r['opening_balance']}*{r['monthly_rate']}",
        "principal": f"=MAX(0,{r['target_ds']}-{r['interest_per_repayment']})",
        "closing_balance": (
            f"={r['opening_balance']}+{r['drawdown']}"
            f"-{r['principal']}-{r['cash_sweep']}"
        ),
        "debt_service": f"={r['interest']}+{r['principal']}+{r['cash_sweep']}",
        "arrangement_fee": (
            f"=IF({r['period']}={r['refi_period']},"
            f"{r['original_balance']}*{r['fee_pct']},0)"
        ),
        "llcr": f"={r['pv_cfads']}/{r['opening_balance']}",
    }
