"""
MODULE_ID:    DEBT_LINEAR_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Multi-facility linear (straight-line) senior debt.

Models up to N independent linear debt facilities, each with its own:
  - LTV-based sizing
  - Tenor and grace period
  - Margin (fixed or stepped)
  - Interest rate hedge (swap)
  - Arrangement fee

Outputs per-facility detail and aggregated totals.

Source: Holsted Hybrid vendor model §2.2 C_Financing rows 95-209, 440-496.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# SUB-MODELS
# ============================================================================

class LinearFacility(BaseModel):
    """One independent linear debt facility."""
    name: str = Field(..., description="Facility name, e.g. 'Senior 1'")
    active: bool = Field(True)
    facility_DKKk: float = Field(..., gt=0, description="Facility size DKKk")
    start_period: int = Field(..., ge=0, description="Period when drawdown begins")
    grace_periods: int = Field(0, ge=0, description="Grace months after last drawdown")
    tenor_months: int = Field(..., gt=0, description="Repayment tenor in months")
    repayments_per_year: int = Field(2, ge=1, le=12, description="Semi-annual=2, quarterly=4")
    margin: float = Field(..., description="Annual margin")
    base_rate: float = Field(0.03, ge=0, description="Annual base/reference rate")
    hedge_pct: float = Field(0.0, ge=0.0, le=1.0)
    swap_rate: Optional[float] = Field(None, description="Annual swap rate for hedged portion")
    arrangement_fee_pct: float = Field(0.0, ge=0.0)
    drawdowns: list[float] = Field(..., description="Monthly drawdown schedule DKKk")


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)
    facilities: list[LinearFacility] = Field(..., min_length=1, max_length=10)

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for i, f in enumerate(self.facilities):
            if len(f.drawdowns) != n:
                raise ValueError(
                    f"facilities[{i}].drawdowns length {len(f.drawdowns)} != periods {n}"
                )
            if f.active and abs(sum(f.drawdowns) - f.facility_DKKk) > 0.01:
                raise ValueError(
                    f"facilities[{i}].drawdowns sum {sum(f.drawdowns):.1f} "
                    f"!= facility_DKKk {f.facility_DKKk:.1f}"
                )
        return self


class FacilityResult(BaseModel):
    """Per-facility output detail."""
    name: str
    facility_DKKk: float
    opening_balance: list[float]
    drawdown: list[float]
    interest: list[float]
    principal: list[float]
    closing_balance: list[float]
    debt_service: list[float]
    arrangement_fee: list[float]
    total_principal: float
    total_interest: float


class Outputs(BaseModel):
    # Per-facility detail
    facility_results: list[FacilityResult]

    # Aggregated across all active facilities
    total_opening_balance: list[float]
    total_drawdown: list[float]
    total_interest: list[float]
    total_principal: list[float]
    total_closing_balance: list[float]
    total_debt_service: list[float]
    total_arrangement_fees: list[float]

    # Scalars
    aggregate_facility_DKKk: float
    aggregate_total_principal: float
    aggregate_total_interest: float


# ============================================================================
# HELPERS
# ============================================================================

def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _infer_repayment_start(drawdowns: list[float]) -> int:
    """First period after the last non-zero drawdown."""
    last = max((i for i, d in enumerate(drawdowns) if d > 1e-9), default=-1)
    return last + 1


def _is_repayment_month(
    p: int, start_month: int, repayments_per_year: int,
) -> bool:
    """Check if period p falls on a repayment month."""
    if repayments_per_year >= 12:
        return True
    interval = 12 // repayments_per_year
    cal_month = ((start_month - 1 + p) % 12) + 1
    return cal_month % interval == 0


def _calc_facility(
    f: LinearFacility, n: int, start_month: int,
) -> FacilityResult:
    """Compute schedule for one linear facility."""
    if not f.active:
        return FacilityResult(
            name=f.name, facility_DKKk=f.facility_DKKk,
            opening_balance=_zeros(n), drawdown=_zeros(n),
            interest=_zeros(n), principal=_zeros(n),
            closing_balance=_zeros(n), debt_service=_zeros(n),
            arrangement_fee=_zeros(n),
            total_principal=0.0, total_interest=0.0,
        )

    rep_start = _infer_repayment_start(f.drawdowns) + f.grace_periods
    n_repayments = f.tenor_months // (12 // f.repayments_per_year)
    princ_per_repayment = f.facility_DKKk / n_repayments if n_repayments > 0 else 0.0
    rep_end = rep_start + f.tenor_months

    opening = _zeros(n)
    interest_arr = _zeros(n)
    principal_arr = _zeros(n)
    closing = _zeros(n)
    fee_arr = _zeros(n)

    # Arrangement fee at first drawdown
    first_draw = next((p for p in range(n) if f.drawdowns[p] > 1e-9), None)
    if first_draw is not None:
        fee_arr[first_draw] = f.facility_DKKk * f.arrangement_fee_pct

    balance = 0.0
    for p in range(n):
        opening[p] = balance

        # Interest on opening balance
        if balance > 1e-9:
            hedged = balance * f.hedge_pct * (f.swap_rate / 12.0 if f.swap_rate else 0.0)
            unhedged = balance * (1.0 - f.hedge_pct) * (f.base_rate + f.margin) / 12.0
            interest_arr[p] = hedged + unhedged

        # Principal on repayment months
        princ = 0.0
        if rep_start <= p < rep_end and balance > 1e-9:
            if _is_repayment_month(p, start_month, f.repayments_per_year):
                princ = min(princ_per_repayment, balance)
        principal_arr[p] = princ

        balance = balance + f.drawdowns[p] - princ
        closing[p] = balance

    ds = [interest_arr[p] + principal_arr[p] for p in range(n)]

    return FacilityResult(
        name=f.name,
        facility_DKKk=f.facility_DKKk,
        opening_balance=opening,
        drawdown=list(f.drawdowns),
        interest=interest_arr,
        principal=principal_arr,
        closing_balance=closing,
        debt_service=ds,
        arrangement_fee=fee_arr,
        total_principal=sum(principal_arr),
        total_interest=sum(interest_arr),
    )


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    results = [_calc_facility(f, n, inputs.start_month) for f in inputs.facilities]

    # Aggregate across active facilities
    agg_open = _zeros(n)
    agg_draw = _zeros(n)
    agg_int = _zeros(n)
    agg_princ = _zeros(n)
    agg_close = _zeros(n)
    agg_ds = _zeros(n)
    agg_fee = _zeros(n)

    for r in results:
        for p in range(n):
            agg_open[p] += r.opening_balance[p]
            agg_draw[p] += r.drawdown[p]
            agg_int[p] += r.interest[p]
            agg_princ[p] += r.principal[p]
            agg_close[p] += r.closing_balance[p]
            agg_ds[p] += r.debt_service[p]
            agg_fee[p] += r.arrangement_fee[p]

    agg_fac = sum(f.facility_DKKk for f in inputs.facilities if f.active)

    return Outputs(
        facility_results=results,
        total_opening_balance=agg_open,
        total_drawdown=agg_draw,
        total_interest=agg_int,
        total_principal=agg_princ,
        total_closing_balance=agg_close,
        total_debt_service=agg_ds,
        total_arrangement_fees=agg_fee,
        aggregate_facility_DKKk=agg_fac,
        aggregate_total_principal=sum(r.total_principal for r in results),
        aggregate_total_interest=sum(r.total_interest for r in results),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "principal_linear": (
            f"=IF({r['is_repayment_month']},"
            f"{r['facility']}/{r['n_repayments']},0)"
        ),
        "interest_hedged": f"={r['opening']}*{r['hedge_pct']}*{r['swap_rate']}/12",
        "interest_unhedged": (
            f"={r['opening']}*(1-{r['hedge_pct']})"
            f"*({r['base_rate']}+{r['margin']})/12"
        ),
        "closing_balance": f"={r['opening']}+{r['drawdown']}-{r['principal']}",
        "debt_service": f"={r['interest']}+{r['principal']}",
        "arrangement_fee": (
            f"=IF({r['period']}={r['first_draw_period']},"
            f"{r['facility']}*{r['fee_pct']},0)"
        ),
    }
