"""
MODULE_ID:    CONSTR_FINANCE_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Construction finance (bullet) facility.

Models a short-term debt facility drawn during construction and repaid
at COD via refinancing into permanent debt facilities.

Features:
  - Equity first / equity last disbursement sequencing
  - Monthly interest on drawn balance (not capitalised)
  - Commitment fee on undrawn facility
  - Arrangement fee at financial close
  - Bullet repayment at COD
  - IDC tracking for CAPEX accounting

Source: Holsted Hybrid vendor model §2.2 C_Financing rows 52-93.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    active: bool = Field(True)
    facility_DKKk: float = Field(..., gt=0, description="Construction facility size DKKk")

    # Timing
    construction_start_period: int = Field(..., ge=0, description="Period of financial close")
    cod_period: int = Field(..., gt=0, description="Period of COD — bullet repayment here")

    # Drawdown
    capex_monthly: list[float] = Field(
        ..., description="Monthly capex schedule DKKk (length=periods)"
    )
    equity_first: bool = Field(True, description="True = equity drawn before debt")
    total_equity_DKKk: float = Field(..., gt=0, description="Total equity for construction")

    # Rates
    base_rate: float = Field(0.03, ge=0, description="Annual base/reference rate")
    margin: float = Field(..., description="Annual margin")
    commitment_fee_rate: float = Field(0.0, ge=0.0, description="Annual commitment fee on undrawn")
    arrangement_fee_pct: float = Field(0.0, ge=0.0, description="Arrangement fee as % of facility")

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.capex_monthly) != n:
            raise ValueError(
                f"capex_monthly length {len(self.capex_monthly)} != periods {n}"
            )
        if self.cod_period >= n:
            raise ValueError(
                f"cod_period {self.cod_period} >= periods {n}"
            )
        if self.cod_period <= self.construction_start_period:
            raise ValueError("cod_period must be > construction_start_period")
        return self


class Outputs(BaseModel):
    opening_balance: list[float]
    drawdown: list[float]
    repayment: list[float]              # bullet at COD
    closing_balance: list[float]
    interest: list[float]               # monthly interest expense
    commitment_fee: list[float]         # on undrawn portion
    arrangement_fee: list[float]        # at financial close
    equity_drawn: list[float]           # equity disbursement schedule
    idc_cumulative: list[float]         # cumulative IDC during construction

    # Scalars
    total_interest: float
    total_commitment_fees: float
    total_idc: float                    # total IDC to add to CAPEX
    peak_balance: float                 # max drawdown


# ============================================================================
# HELPERS
# ============================================================================

def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _empty_outputs(n: int) -> Outputs:
    return Outputs(
        opening_balance=_zeros(n), drawdown=_zeros(n),
        repayment=_zeros(n), closing_balance=_zeros(n),
        interest=_zeros(n), commitment_fee=_zeros(n),
        arrangement_fee=_zeros(n), equity_drawn=_zeros(n),
        idc_cumulative=_zeros(n),
        total_interest=0.0, total_commitment_fees=0.0,
        total_idc=0.0, peak_balance=0.0,
    )


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods

    if not inputs.active:
        return _empty_outputs(n)

    cs = inputs.construction_start_period
    cod = inputs.cod_period
    fac = inputs.facility_DKKk

    opening = _zeros(n)
    draw = _zeros(n)
    repay = _zeros(n)
    closing = _zeros(n)
    interest_arr = _zeros(n)
    commit_fee = _zeros(n)
    arr_fee = _zeros(n)
    eq_drawn = _zeros(n)
    idc_cum = _zeros(n)

    # Arrangement fee at financial close
    arr_fee[cs] = fac * inputs.arrangement_fee_pct

    # Equity/debt sequencing during construction
    equity_remaining = inputs.total_equity_DKKk
    balance = 0.0

    for p in range(n):
        opening[p] = balance

        # Interest on opening balance
        if balance > 1e-9:
            interest_arr[p] = balance * (inputs.base_rate + inputs.margin) / 12.0

        # Commitment fee on undrawn during construction
        if cs <= p < cod:
            undrawn = max(0.0, fac - balance)
            commit_fee[p] = undrawn * inputs.commitment_fee_rate / 12.0

        # Drawdowns only during construction
        if cs <= p < cod:
            capex_need = inputs.capex_monthly[p]
            if inputs.equity_first:
                eq = min(capex_need, equity_remaining)
                eq_drawn[p] = eq
                equity_remaining -= eq
                debt_need = capex_need - eq
            else:
                debt_need = min(capex_need, fac - balance)
                eq = capex_need - debt_need
                eq_drawn[p] = min(eq, equity_remaining)
                equity_remaining -= eq_drawn[p]
            draw[p] = min(max(0.0, debt_need), fac - balance)

        # Bullet repayment at COD
        if p == cod:
            repay[p] = balance + draw[p]

        balance = balance + draw[p] - repay[p]
        closing[p] = balance

    # IDC cumulative (only during construction)
    cum = 0.0
    for p in range(cs, cod):
        cum += interest_arr[p]
        idc_cum[p] = cum
    # Fill COD period with final IDC
    if cod < n:
        idc_cum[cod] = cum

    peak = max(opening[p] + draw[p] for p in range(n)) if n > 0 else 0.0

    return Outputs(
        opening_balance=opening,
        drawdown=draw,
        repayment=repay,
        closing_balance=closing,
        interest=interest_arr,
        commitment_fee=commit_fee,
        arrangement_fee=arr_fee,
        equity_drawn=eq_drawn,
        idc_cumulative=idc_cum,
        total_interest=sum(interest_arr),
        total_commitment_fees=sum(commit_fee),
        total_idc=cum,
        peak_balance=peak,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "drawdown_equity_first": (
            f"=MIN({r['capex']}-{r['equity_draw']},"
            f"{r['facility']}-{r['balance']})"
        ),
        "interest": f"={r['opening']}*({r['base_rate']}+{r['margin']})/12",
        "commitment_fee": (
            f"=MAX(0,{r['facility']}-{r['opening']})"
            f"*{r['commit_rate']}/12"
        ),
        "repayment_bullet": (
            f"=IF({r['period']}={r['cod']},"
            f"{r['opening']}+{r['drawdown']},0)"
        ),
        "closing_balance": (
            f"={r['opening']}+{r['drawdown']}-{r['repayment']}"
        ),
        "arrangement_fee": (
            f"=IF({r['period']}={r['construction_start']},"
            f"{r['facility']}*{r['fee_pct']},0)"
        ),
    }
