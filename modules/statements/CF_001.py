"""
MODULE_ID:    CF_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Cash Flow statement — monthly, indirect method per EE_MODEL_BUILD_SPEC.md §9.

Structure:
    OPERATING CASH FLOW
        Net Income
      + Depreciation & Amortisation       (non-cash add-back)
      + Working Capital Change             (negative = cash absorbed)
      = Cash Flow from Operations (CFO)

    INVESTING CASH FLOW
      − Capital Expenditure               (negative = cash out)
      = Cash Flow from Investing (CFI)

    FINANCING CASH FLOW
      + Debt Drawdown
      − Principal Repayment
      − Interest Paid                     (cash interest, may differ from accrued)
      − Dividends Paid
      = Cash Flow from Financing (CFF)

    NET CASH FLOW  = CFO + CFI + CFF
    OPENING CASH   = prior period closing cash
    CLOSING CASH   = opening + net cash flow

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9
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

    # Opening cash balance (DKKk)
    opening_cash_DKKk: float = Field(0.0, description="Cash balance at start of period 0")

    # Operating
    net_income: list[float] = Field(..., description="Monthly net income DKKk")
    depreciation: list[float] = Field(..., description="Monthly D&A add-back DKKk")
    working_capital_change: list[float] = Field(
        default_factory=list,
        description="Monthly working capital change DKKk (negative = cash absorbed)"
    )

    # Investing
    capex_monthly: list[float] = Field(
        ..., description="Monthly capital expenditure DKKk (positive = cash out)"
    )

    # Financing
    debt_drawdown: list[float] = Field(
        default_factory=list,
        description="Monthly debt drawdown received DKKk (positive = cash in)"
    )
    principal_repayment: list[float] = Field(
        default_factory=list,
        description="Monthly principal repayment DKKk (positive = cash out)"
    )
    interest_paid: list[float] = Field(
        ..., description="Monthly cash interest paid DKKk (positive = cash out)"
    )
    dividends_paid: list[float] = Field(
        default_factory=list,
        description="Monthly dividends paid DKKk (positive = cash out)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        required = {
            "net_income": self.net_income,
            "depreciation": self.depreciation,
            "capex_monthly": self.capex_monthly,
            "interest_paid": self.interest_paid,
        }
        for name, lst in required.items():
            if len(lst) != n:
                raise ValueError(f"{name} length {len(lst)} != periods {n}")

        # Optional series — default to zeros if empty
        for name in ("working_capital_change", "debt_drawdown",
                     "principal_repayment", "dividends_paid"):
            lst = getattr(self, name)
            if lst and len(lst) != n:
                raise ValueError(f"{name} length {len(lst)} != periods {n}")

        return self

    def _zeros(self, name: str) -> list[float]:
        lst = getattr(self, name)
        return lst if lst else [0.0] * self.periods


class Outputs(BaseModel):
    # Operating (monthly, DKKk)
    net_income: list[float]
    depreciation: list[float]
    working_capital_change: list[float]
    cfo: list[float]    # Cash Flow from Operations

    # Investing (monthly, DKKk)
    capex_monthly: list[float]
    cfi: list[float]    # Cash Flow from Investing

    # Financing (monthly, DKKk)
    debt_drawdown: list[float]
    principal_repayment: list[float]
    interest_paid: list[float]
    dividends_paid: list[float]
    cff: list[float]    # Cash Flow from Financing

    # Net and balance
    net_cash_flow: list[float]
    opening_cash: list[float]
    closing_cash: list[float]

    # Annual aggregations
    annual_cfo: list[float]
    annual_cfi: list[float]
    annual_cff: list[float]
    annual_net_cash_flow: list[float]

    # Lifetime totals
    lifetime_cfo: float
    lifetime_cfi: float
    lifetime_cff: float


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
    """Build monthly cash flow statement with running cash balance."""
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    ni = inputs.net_income
    dep = inputs.depreciation
    wc = inputs._zeros("working_capital_change")
    capex = inputs.capex_monthly
    dd = inputs._zeros("debt_drawdown")
    pr = inputs._zeros("principal_repayment")
    ip = inputs.interest_paid
    div = inputs._zeros("dividends_paid")

    # Operating
    cfo = [ni[p] + dep[p] + wc[p] for p in range(n)]

    # Investing (capex is positive = cash out, so subtract)
    cfi = [-capex[p] for p in range(n)]

    # Financing
    cff = [dd[p] - pr[p] - ip[p] - div[p] for p in range(n)]

    # Net cash and balance
    net = [cfo[p] + cfi[p] + cff[p] for p in range(n)]
    opening = [0.0] * n
    closing = [0.0] * n
    opening[0] = inputs.opening_cash_DKKk
    closing[0] = opening[0] + net[0]
    for p in range(1, n):
        opening[p] = closing[p - 1]
        closing[p] = opening[p] + net[p]

    return Outputs(
        net_income=ni,
        depreciation=dep,
        working_capital_change=wc,
        cfo=cfo,
        capex_monthly=capex,
        cfi=cfi,
        debt_drawdown=dd,
        principal_repayment=pr,
        interest_paid=ip,
        dividends_paid=div,
        cff=cff,
        net_cash_flow=net,
        opening_cash=opening,
        closing_cash=closing,
        annual_cfo=_annual(cfo, yg),
        annual_cfi=_annual(cfi, yg),
        annual_cff=_annual(cff, yg),
        annual_net_cash_flow=_annual(net, yg),
        lifetime_cfo=sum(cfo),
        lifetime_cfi=sum(cfi),
        lifetime_cff=sum(cff),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    refs keys: net_income, depreciation, wc_change, capex,
               debt_drawdown, principal, interest_paid, dividends,
               prev_closing_cash, net_cash_flow
    """
    r = refs
    return {
        "cfo":            f"={r['net_income']}+{r['depreciation']}+{r['wc_change']}",
        "cfi":            f"=-{r['capex']}",
        "cff":            f"={r['debt_drawdown']}-{r['principal']}-{r['interest_paid']}-{r['dividends']}",
        "net_cash_flow":  f"={r['cfo']}+{r['cfi']}+{r['cff']}",
        "opening_cash":   f"={r['prev_closing_cash']}",
        "closing_cash":   f"={r['prev_closing_cash']}+{r['net_cash_flow']}",
    }
