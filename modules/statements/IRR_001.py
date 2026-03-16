"""
MODULE_ID:    IRR_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

DCF Valuation — project IRR, equity IRR, NPV and payback per EE_MODEL_BUILD_SPEC.md §11.

Definitions:
    Project Free Cash Flow (PFCF)  = CFO − Capex  (unlevered, pre-financing)
    Equity Cash Flow (ECF)         = PFCF − Interest Paid − Principal Repayment
                                      + Debt Drawdown − Dividends Paid
                                   = Net Cash Flow from CF_001 (i.e. closing_cash change)

    Project IRR    = monthly IRR on PFCF series (annualised: (1+r_m)^12 − 1)
    Equity IRR     = monthly IRR on ECF series
    Project NPV    = NPV of PFCF discounted at project_discount_rate
    Equity NPV     = NPV of ECF discounted at equity_discount_rate
    Payback Period = first period where cumulative PFCF ≥ 0 (months from period 0)

IRR is solved by bisection (no external dependencies).
NPV discounting uses continuous monthly compounding:
    monthly_rate = (1 + annual_rate)^(1/12) − 1

Source: EE_MODEL_BUILD_SPEC.md v2.0 §11
"""

from __future__ import annotations

import math
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)

    # Cash flow series (DKKk, monthly)
    project_free_cash_flow: list[float] = Field(
        ..., description="Monthly unlevered free cash flow DKKk (CFO − Capex)"
    )
    equity_cash_flow: list[float] = Field(
        ..., description="Monthly equity cash flow DKKk (net CF to equity holders)"
    )

    # Discount rates (annual)
    project_discount_rate: float = Field(
        0.07, ge=0, description="Annual project discount rate (WACC) for NPV"
    )
    equity_discount_rate: float = Field(
        0.10, ge=0, description="Annual equity discount rate (CoE) for equity NPV"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.project_free_cash_flow) != n:
            raise ValueError(
                f"project_free_cash_flow length {len(self.project_free_cash_flow)} != periods {n}"
            )
        if len(self.equity_cash_flow) != n:
            raise ValueError(
                f"equity_cash_flow length {len(self.equity_cash_flow)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    # IRR (annual, decimal — e.g. 0.085 = 8.5%)
    project_irr: float          # annualised monthly IRR on PFCF; None→nan if no solution
    equity_irr: float           # annualised monthly IRR on ECF

    # NPV (DKKk)
    project_npv: float
    equity_npv: float

    # Payback
    payback_period_months: int  # −1 if never recovered

    # Cumulative PFCF series (DKKk) — for payback and J-curve analysis
    cumulative_pfcf: list[float]

    # Annualised PFCF (one value per calendar year equivalent of 12 months)
    annual_pfcf: list[float]
    annual_ecf: list[float]


# ============================================================================
# HELPERS
# ============================================================================

def _npv(cash_flows: list[float], monthly_rate: float) -> float:
    """NPV of a monthly cash flow series at given monthly discount rate."""
    if monthly_rate == 0.0:
        return sum(cash_flows)
    total = 0.0
    discount = 1.0
    for cf in cash_flows:
        total += cf / discount
        discount *= (1.0 + monthly_rate)
        if discount == 0.0:
            break
    return total


def _irr_monthly(cash_flows: list[float], tol: float = 1e-8, max_iter: int = 200) -> float:
    """
    Solve for monthly IRR using bisection on NPV = 0.
    Returns float('nan') if no sign change found (no real solution).
    Search range: −50% to +100% monthly (covers all realistic project IRRs).
    """
    # Quick check: need at least one sign change in cash flows
    has_pos = any(cf > 0 for cf in cash_flows)
    has_neg = any(cf < 0 for cf in cash_flows)
    if not (has_pos and has_neg):
        return float("nan")

    lo, hi = -0.5, 1.0   # monthly rate bounds: −50% to +100%
    npv_lo = _npv(cash_flows, lo)
    npv_hi = _npv(cash_flows, hi)

    if npv_lo * npv_hi > 0:
        # No sign change — try tighter range
        hi = 1.0
        npv_hi = _npv(cash_flows, hi)
        if npv_lo * npv_hi > 0:
            return float("nan")

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        npv_mid = _npv(cash_flows, mid)
        if abs(npv_mid) < tol or (hi - lo) / 2.0 < tol:
            return mid
        if npv_lo * npv_mid < 0:
            hi = mid
            npv_hi = npv_mid
        else:
            lo = mid
            npv_lo = npv_mid

    return (lo + hi) / 2.0


def _annualise_irr(monthly_irr: float) -> float:
    """Convert monthly IRR to annual: (1 + r_m)^12 − 1."""
    if math.isnan(monthly_irr):
        return float("nan")
    return (1.0 + monthly_irr) ** 12 - 1.0


def _monthly_rate(annual_rate: float) -> float:
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


def _annual_sums(series: list[float], periods_per_year: int = 12) -> list[float]:
    """Group monthly series into annual sums (last group may be partial)."""
    result = []
    for i in range(0, len(series), periods_per_year):
        result.append(sum(series[i:i + periods_per_year]))
    return result


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Compute project and equity IRR, NPV, payback, and cumulative PFCF."""
    pfcf = inputs.project_free_cash_flow
    ecf = inputs.equity_cash_flow
    n = inputs.periods

    # IRR (monthly → annualised)
    proj_irr_m = _irr_monthly(pfcf)
    eq_irr_m   = _irr_monthly(ecf)
    project_irr = _annualise_irr(proj_irr_m)
    equity_irr  = _annualise_irr(eq_irr_m)

    # NPV
    proj_mr = _monthly_rate(inputs.project_discount_rate)
    eq_mr   = _monthly_rate(inputs.equity_discount_rate)
    project_npv = _npv(pfcf, proj_mr)
    equity_npv  = _npv(ecf, eq_mr)

    # Cumulative PFCF and payback
    cum_pfcf = []
    running = 0.0
    payback = -1
    for p in range(n):
        running += pfcf[p]
        cum_pfcf.append(running)
        if payback == -1 and running >= 0.0:
            payback = p

    # Annual sums
    annual_pfcf = _annual_sums(pfcf)
    annual_ecf  = _annual_sums(ecf)

    return Outputs(
        project_irr=project_irr,
        equity_irr=equity_irr,
        project_npv=project_npv,
        equity_npv=equity_npv,
        payback_period_months=payback,
        cumulative_pfcf=cum_pfcf,
        annual_pfcf=annual_pfcf,
        annual_ecf=annual_ecf,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    refs keys: pfcf_range, ecf_range, project_rate, equity_rate,
               prev_cum_pfcf, pfcf
    """
    r = refs
    return {
        "project_irr":   f"=(1+IRR({r['pfcf_range']},0.005))^12-1",
        "equity_irr":    f"=(1+IRR({r['ecf_range']},0.005))^12-1",
        "project_npv":   f"=NPV({r['project_rate']}/12,{r['pfcf_range']})",
        "equity_npv":    f"=NPV({r['equity_rate']}/12,{r['ecf_range']})",
        "cumulative_pfcf": f"={r['prev_cum_pfcf']}+{r['pfcf']}",
    }
