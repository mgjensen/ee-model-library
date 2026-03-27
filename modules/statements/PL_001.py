"""
MODULE_ID:    PL_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Profit & Loss statement — monthly accrual basis per EE_MODEL_BUILD_SPEC.md §8.

Structure:
    Gross Revenue
    − Total OPEX
    = EBITDA
    − Depreciation & Amortisation
    = EBIT
    − Interest Expense (net)
    = EBT  (Earnings Before Tax)
    − Tax Charge
    = Net Income

All inputs are monthly time-series (length = periods).
All output line items are provided as monthly, annual, and lifetime aggregations.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §8
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)

    # Monthly P&L line items (DKKk, positive = income / positive charge)
    gross_revenue: list[float] = Field(..., description="Total revenue DKKk/month")
    total_opex: list[float] = Field(..., description="Total operating costs DKKk/month (positive = cost)")
    depreciation: list[float] = Field(..., description="D&A charge DKKk/month (positive = charge)")
    interest_expense: list[float] = Field(..., description="Net interest expense DKKk/month (positive = expense)")
    tax_charge: list[float] = Field(..., description="Corporate tax charge DKKk/month (positive = charge)")

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for name, lst in [
            ("gross_revenue", self.gross_revenue),
            ("total_opex", self.total_opex),
            ("depreciation", self.depreciation),
            ("interest_expense", self.interest_expense),
            ("tax_charge", self.tax_charge),
        ]:
            if len(lst) != n:
                raise ValueError(f"{name} length {len(lst)} != periods {n}")
        return self


class Outputs(BaseModel):
    # Monthly P&L (length = periods)
    gross_revenue: list[float]
    total_opex: list[float]
    ebitda: list[float]
    depreciation: list[float]
    ebit: list[float]
    interest_expense: list[float]
    ebt: list[float]
    tax_charge: list[float]
    net_income: list[float]

    # Annual aggregations (one per calendar year)
    annual_gross_revenue: list[float]
    annual_total_opex: list[float]
    annual_ebitda: list[float]
    annual_depreciation: list[float]
    annual_ebit: list[float]
    annual_interest_expense: list[float]
    annual_ebt: list[float]
    annual_tax_charge: list[float]
    annual_net_income: list[float]

    # Lifetime totals
    lifetime_gross_revenue: float
    lifetime_total_opex: float
    lifetime_ebitda: float
    lifetime_net_income: float


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


def _annual(series: list[float], yg: OrderedDict) -> list[float]:
    return [sum(series[p] for p in plist) for plist in yg.values()]


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Build monthly P&L and roll up to annual and lifetime totals."""
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    rev = inputs.gross_revenue
    opex = inputs.total_opex
    dep = inputs.depreciation
    int_exp = inputs.interest_expense
    tax = inputs.tax_charge

    ebitda = [rev[p] - opex[p] for p in range(n)]
    ebit   = [ebitda[p] - dep[p] for p in range(n)]
    ebt    = [ebit[p] - int_exp[p] for p in range(n)]
    net    = [ebt[p] - tax[p] for p in range(n)]

    return Outputs(
        gross_revenue=rev,
        total_opex=opex,
        ebitda=ebitda,
        depreciation=dep,
        ebit=ebit,
        interest_expense=int_exp,
        ebt=ebt,
        tax_charge=tax,
        net_income=net,
        annual_gross_revenue=_annual(rev, yg),
        annual_total_opex=_annual(opex, yg),
        annual_ebitda=_annual(ebitda, yg),
        annual_depreciation=_annual(dep, yg),
        annual_ebit=_annual(ebit, yg),
        annual_interest_expense=_annual(int_exp, yg),
        annual_ebt=_annual(ebt, yg),
        annual_tax_charge=_annual(tax, yg),
        annual_net_income=_annual(net, yg),
        lifetime_gross_revenue=sum(rev),
        lifetime_total_opex=sum(opex),
        lifetime_ebitda=sum(ebitda),
        lifetime_net_income=sum(net),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    refs keys: gross_revenue, total_opex, ebitda, depreciation,
               ebit, interest_expense, ebt, tax_charge
    """
    r = refs
    return {
        "ebitda":     f"={r['gross_revenue']}-{r['total_opex']}",
        "ebit":       f"={r['ebitda']}-{r['depreciation']}",
        "ebt":        f"={r['ebit']}-{r['interest_expense']}",
        "net_income": f"={r['ebt']}-{r['tax_charge']}",
    }
