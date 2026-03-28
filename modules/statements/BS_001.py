"""
MODULE_ID:    BS_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Balance Sheet — monthly snapshot per EE_MODEL_BUILD_SPEC.md §10.

Structure:
    ASSETS
        Fixed Assets — Gross                 = cumulative capex
        Accumulated Depreciation             = cumulative D&A (negative)
        Fixed Assets — Net                   = gross + accumulated_dep
        Cash & Equivalents                   = closing cash from CF_001
        Total Assets                         = net_fixed_assets + cash

    LIABILITIES & EQUITY
        Debt Balance                         = from DEBT_001 closing balance
        Contributed Equity                   = opening equity (constant)
        Retained Earnings                    = cumulative net income − cumulative dividends
        Total Equity                         = contributed + retained earnings
        Total Liabilities & Equity           = debt + total_equity

    BALANCE CHECK
        Imbalance                            = total_assets − total_liabilities_equity
                                             (should be ≈ 0 each period)

Notes:
    - This module receives pre-computed series from other modules; it does not
      re-derive P&L or cash flow items.
    - Accumulated depreciation is stored as a negative number (reducing assets).
    - The balance check will be non-zero only if inputs from other modules
      are inconsistent (e.g., missing accruals). A tolerance of ±1 DKKk is
      acceptable for floating-point rounding.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §10
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

    # Opening balance sheet (DKKk)
    opening_contributed_equity_DKKk: float = Field(
        0.0, description="Equity injected at financial close (constant throughout life)"
    )
    opening_retained_earnings_DKKk: float = Field(
        0.0, description="Retained earnings carried in at start"
    )

    # Retrofit opening balances — for projects with prior operating history
    opening_fixed_assets_gross_DKKk: float = Field(
        0.0, description="Gross fixed assets carried in from prior operations (cumulative capex before model start)"
    )
    opening_accumulated_depreciation_DKKk: float = Field(
        0.0, le=0, description="Accumulated depreciation at model start (negative, reducing assets)"
    )
    opening_debt_balance_DKKk: float = Field(
        0.0, description="Debt balance carried in at model start (added to period 0 debt)"
    )

    # From CAPEX_001 — monthly capex spend (DKKk, positive = spend)
    capex_monthly: list[float] = Field(..., description="Monthly capex DKKk")

    # From TAX_001 / PL_001 — monthly D&A charge (DKKk, positive = charge)
    depreciation_monthly: list[float] = Field(..., description="Monthly D&A charge DKKk")

    # From CF_001 — closing cash each period (DKKk)
    closing_cash: list[float] = Field(..., description="Closing cash balance DKKk from CF_001")

    # From DEBT_001 — closing debt balance each period (DKKk)
    debt_closing_balance: list[float] = Field(..., description="Closing debt balance DKKk from DEBT_001")

    # From PL_001 — monthly net income (DKKk)
    net_income: list[float] = Field(..., description="Monthly net income DKKk from PL_001")

    # Dividends paid each period (DKKk, positive = cash out)
    dividends_paid: list[float] = Field(
        default_factory=list,
        description="Monthly dividends paid DKKk (optional)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        required = {
            "capex_monthly": self.capex_monthly,
            "depreciation_monthly": self.depreciation_monthly,
            "closing_cash": self.closing_cash,
            "debt_closing_balance": self.debt_closing_balance,
            "net_income": self.net_income,
        }
        for name, lst in required.items():
            if len(lst) != n:
                raise ValueError(f"{name} length {len(lst)} != periods {n}")
        if self.dividends_paid and len(self.dividends_paid) != n:
            raise ValueError(f"dividends_paid length {len(self.dividends_paid)} != periods {n}")
        return self

    def _zeros(self, name: str) -> list[float]:
        lst = getattr(self, name)
        return lst if lst else [0.0] * self.periods


class Outputs(BaseModel):
    # Assets (monthly snapshot, DKKk)
    fixed_assets_gross: list[float]         # cumulative capex
    accumulated_depreciation: list[float]   # cumulative D&A (negative)
    fixed_assets_net: list[float]           # gross + accumulated_dep
    cash: list[float]                       # = closing_cash from CF_001
    total_assets: list[float]

    # Liabilities & Equity (monthly snapshot, DKKk)
    debt_balance: list[float]               # = debt_closing_balance from DEBT_001
    contributed_equity: list[float]         # constant = opening_contributed_equity
    retained_earnings: list[float]          # cumulative net income − cumulative dividends
    total_equity: list[float]
    total_liabilities_equity: list[float]

    # Balance check
    imbalance: list[float]                  # total_assets − total_liabilities_equity (≈ 0)
    max_imbalance: float                    # max(abs(imbalance)) — diagnostic scalar


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
    """Build monthly balance sheet snapshots and verify balance."""
    n = inputs.periods
    div = inputs._zeros("dividends_paid")

    contributed = inputs.opening_contributed_equity_DKKk

    # Cumulative capex (fixed assets gross), seeded with opening balance
    fa_gross = []
    cumcapex = inputs.opening_fixed_assets_gross_DKKk
    for p in range(n):
        cumcapex += inputs.capex_monthly[p]
        fa_gross.append(cumcapex)

    # Cumulative depreciation (negative), seeded with opening balance
    acc_dep = []
    cumdep = inputs.opening_accumulated_depreciation_DKKk  # already negative
    for p in range(n):
        cumdep -= inputs.depreciation_monthly[p]
        acc_dep.append(cumdep)

    fa_net = [fa_gross[p] + acc_dep[p] for p in range(n)]

    cash = inputs.closing_cash
    total_assets = [fa_net[p] + cash[p] for p in range(n)]

    # Debt balance: passthrough + optional opening balance offset
    if inputs.opening_debt_balance_DKKk != 0:
        debt = [inputs.debt_closing_balance[p] + inputs.opening_debt_balance_DKKk
                for p in range(n)]
    else:
        debt = inputs.debt_closing_balance

    # Retained earnings: opening + cumulative(net_income - dividends)
    retained = []
    re = inputs.opening_retained_earnings_DKKk
    for p in range(n):
        re += inputs.net_income[p] - div[p]
        retained.append(re)

    contributed_series = [contributed] * n
    total_equity = [contributed + retained[p] for p in range(n)]
    total_le = [debt[p] + total_equity[p] for p in range(n)]

    imbalance = [total_assets[p] - total_le[p] for p in range(n)]
    max_imb = max(abs(v) for v in imbalance)

    return Outputs(
        fixed_assets_gross=fa_gross,
        accumulated_depreciation=acc_dep,
        fixed_assets_net=fa_net,
        cash=cash,
        total_assets=total_assets,
        debt_balance=debt,
        contributed_equity=contributed_series,
        retained_earnings=retained,
        total_equity=total_equity,
        total_liabilities_equity=total_le,
        imbalance=imbalance,
        max_imbalance=max_imb,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    refs keys: prev_fa_gross, capex, prev_acc_dep, depreciation,
               fa_gross, acc_dep, cash, debt, contributed_equity,
               prev_retained, net_income, dividends, total_equity,
               total_assets, total_liabilities_equity
    """
    r = refs
    return {
        "fixed_assets_gross":       f"={r['prev_fa_gross']}+{r['capex']}",
        "accumulated_depreciation": f"={r['prev_acc_dep']}-{r['depreciation']}",
        "fixed_assets_net":         f"={r['fa_gross']}+{r['acc_dep']}",
        "total_assets":             f"={r['fa_gross']}+{r['acc_dep']}+{r['cash']}",
        "retained_earnings":        f"={r['prev_retained']}+{r['net_income']}-{r['dividends']}",
        "total_equity":             f"={r['contributed_equity']}+{r['prev_retained']}+{r['net_income']}-{r['dividends']}",
        "total_liabilities_equity": f"={r['debt']}+{r['total_equity']}",
        "imbalance":                f"={r['total_assets']}-{r['total_liabilities_equity']}",
    }
