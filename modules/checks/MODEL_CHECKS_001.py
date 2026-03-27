"""
MODULE_ID:    MODEL_CHECKS_001
VERSION:      1.0
TIER:         both
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Automated model integrity and commercial checks.

Matches vendor model 1.4 Global rows 37-62.  Runs after all other modules
and validates balance-sheet alignment, cash positivity, income-statement
consistency, depreciation vs capex, DSCR covenant, sources & uses, debt
positivity, and capex budget adherence.
"""

from __future__ import annotations

import math
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-model
# ---------------------------------------------------------------------------

class CheckResult(BaseModel):
    name: str
    category: str  # "integrity" | "commercial"
    passed: bool
    detail: str
    max_deviation: float = 0.0


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    tolerance: float = Field(1.0, gt=0, description="DKKk tolerance for balance checks")

    # Balance sheet
    total_assets: list[float] = Field(default_factory=list)
    total_liabilities_equity: list[float] = Field(default_factory=list)

    # Cash
    closing_cash: list[float] = Field(default_factory=list)

    # Debt
    debt_closing_balance: list[float] = Field(default_factory=list)

    # DSCR
    dscr_series: list[float] = Field(default_factory=list)
    dscr_covenant: float = Field(1.10)

    # Income statement
    gross_revenue: list[float] = Field(default_factory=list)
    total_opex: list[float] = Field(default_factory=list)
    depreciation: list[float] = Field(default_factory=list)
    interest_expense: list[float] = Field(default_factory=list)
    tax_charge: list[float] = Field(default_factory=list)
    net_income: list[float] = Field(default_factory=list)

    # Capex
    capex_monthly: list[float] = Field(default_factory=list)
    cumulative_capex: list[float] = Field(default_factory=list)
    total_capex_budget: float = Field(0.0)
    cumulative_depreciation: list[float] = Field(default_factory=list)

    # S&U
    sources_uses_balance: float = Field(0.0)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

class Outputs(BaseModel):
    checks: list[CheckResult]
    integrity_pass: bool
    commercial_pass: bool
    overall_pass: bool
    summary: str


# ---------------------------------------------------------------------------
# calculate
# ---------------------------------------------------------------------------

def calculate(inputs: Inputs) -> Outputs:
    """Run all model integrity and commercial checks."""
    n = inputs.periods
    tol = inputs.tolerance
    checks: list[CheckResult] = []

    # 1. Balance Sheet Alignment
    if inputs.total_assets and inputs.total_liabilities_equity:
        devs = [
            abs(inputs.total_assets[p] - inputs.total_liabilities_equity[p])
            for p in range(min(len(inputs.total_assets), len(inputs.total_liabilities_equity)))
        ]
        max_dev = max(devs) if devs else 0.0
        passed = max_dev < tol
        checks.append(CheckResult(
            name="Balance Sheet Alignment",
            category="integrity",
            passed=passed,
            detail=f"Max deviation {max_dev:.2f} DKKk" if passed else f"FAIL: max deviation {max_dev:.2f} DKKk >= tolerance {tol:.2f}",
            max_deviation=max_dev,
        ))
    else:
        checks.append(CheckResult(
            name="Balance Sheet Alignment",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # 2. Negative Cash Balance
    if inputs.closing_cash:
        min_cash = min(inputs.closing_cash)
        passed = min_cash >= -tol
        checks.append(CheckResult(
            name="Negative Cash Balance",
            category="integrity",
            passed=passed,
            detail=f"Min cash {min_cash:.2f} DKKk" if passed else f"FAIL: min cash {min_cash:.2f} DKKk < -{tol:.2f}",
            max_deviation=abs(min_cash) if min_cash < 0 else 0.0,
        ))
    else:
        checks.append(CheckResult(
            name="Negative Cash Balance",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # 3. Income Statement Consistency
    is_arrays = [
        inputs.gross_revenue, inputs.total_opex, inputs.depreciation,
        inputs.interest_expense, inputs.tax_charge, inputs.net_income,
    ]
    if all(arr for arr in is_arrays):
        length = min(len(a) for a in is_arrays)
        devs = []
        for p in range(length):
            implied = (
                inputs.gross_revenue[p]
                - inputs.total_opex[p]
                - inputs.depreciation[p]
                - inputs.interest_expense[p]
                - inputs.tax_charge[p]
            )
            devs.append(abs(implied - inputs.net_income[p]))
        max_dev = max(devs) if devs else 0.0
        passed = max_dev < tol
        checks.append(CheckResult(
            name="Income Statement Consistency",
            category="integrity",
            passed=passed,
            detail=f"Max deviation {max_dev:.2f} DKKk" if passed else f"FAIL: max deviation {max_dev:.2f} DKKk >= tolerance {tol:.2f}",
            max_deviation=max_dev,
        ))
    else:
        checks.append(CheckResult(
            name="Income Statement Consistency",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # 4. Depreciation vs Capex
    if inputs.cumulative_depreciation and inputs.cumulative_capex:
        max_dep = max(inputs.cumulative_depreciation)
        max_cap = max(inputs.cumulative_capex)
        passed = max_dep <= max_cap + tol
        dev = max(0.0, max_dep - max_cap)
        checks.append(CheckResult(
            name="Depreciation vs Capex",
            category="integrity",
            passed=passed,
            detail=f"Max cum. dep {max_dep:.2f} vs max cum. capex {max_cap:.2f}" if passed else f"FAIL: cum. depreciation {max_dep:.2f} > cum. capex {max_cap:.2f} + tol {tol:.2f}",
            max_deviation=dev,
        ))
    else:
        checks.append(CheckResult(
            name="Depreciation vs Capex",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # 5. DSCR Covenant
    if inputs.dscr_series:
        valid = [v for v in inputs.dscr_series if not math.isnan(v)]
        if valid:
            min_dscr = min(valid)
            passed = min_dscr >= inputs.dscr_covenant
            checks.append(CheckResult(
                name="DSCR Covenant",
                category="commercial",
                passed=passed,
                detail=f"Min DSCR {min_dscr:.2f}x vs covenant {inputs.dscr_covenant:.2f}x" if passed else f"FAIL: min DSCR {min_dscr:.2f}x < covenant {inputs.dscr_covenant:.2f}x",
                max_deviation=max(0.0, inputs.dscr_covenant - min_dscr),
            ))
        else:
            checks.append(CheckResult(
                name="DSCR Covenant",
                category="commercial",
                passed=True,
                detail="Not checked - no data",
            ))
    else:
        checks.append(CheckResult(
            name="DSCR Covenant",
            category="commercial",
            passed=True,
            detail="Not checked - no data",
        ))

    # 6. Sources & Uses Balanced
    su_dev = abs(inputs.sources_uses_balance)
    su_passed = su_dev < tol
    checks.append(CheckResult(
        name="Sources & Uses Balanced",
        category="integrity",
        passed=su_passed,
        detail=f"S&U balance {inputs.sources_uses_balance:.2f} DKKk" if su_passed else f"FAIL: S&U imbalance {inputs.sources_uses_balance:.2f} DKKk >= tolerance {tol:.2f}",
        max_deviation=su_dev,
    ))

    # 7. No Negative Debt Balance
    if inputs.debt_closing_balance:
        min_debt = min(inputs.debt_closing_balance)
        passed = min_debt >= -tol
        checks.append(CheckResult(
            name="No Negative Debt Balance",
            category="integrity",
            passed=passed,
            detail=f"Min debt balance {min_debt:.2f} DKKk" if passed else f"FAIL: min debt balance {min_debt:.2f} DKKk < -{tol:.2f}",
            max_deviation=abs(min_debt) if min_debt < 0 else 0.0,
        ))
    else:
        checks.append(CheckResult(
            name="No Negative Debt Balance",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # 8. Capex Within Budget
    if inputs.cumulative_capex and inputs.total_capex_budget > 0:
        max_cum = max(inputs.cumulative_capex)
        passed = max_cum <= inputs.total_capex_budget + tol
        dev = max(0.0, max_cum - inputs.total_capex_budget)
        checks.append(CheckResult(
            name="Capex Within Budget",
            category="integrity",
            passed=passed,
            detail=f"Max cum. capex {max_cum:.2f} vs budget {inputs.total_capex_budget:.2f}" if passed else f"FAIL: cum. capex {max_cum:.2f} > budget {inputs.total_capex_budget:.2f} + tol {tol:.2f}",
            max_deviation=dev,
        ))
    else:
        checks.append(CheckResult(
            name="Capex Within Budget",
            category="integrity",
            passed=True,
            detail="Not checked - no data",
        ))

    # Aggregation
    integrity = [c for c in checks if c.category == "integrity"]
    commercial = [c for c in checks if c.category == "commercial"]
    integrity_pass = all(c.passed for c in integrity)
    commercial_pass = all(c.passed for c in commercial)
    overall_pass = integrity_pass and commercial_pass

    if overall_pass:
        summary = "OK"
    else:
        failed = [c.name for c in checks if not c.passed]
        summary = "FAIL: " + ", ".join(failed)

    return Outputs(
        checks=checks,
        integrity_pass=integrity_pass,
        commercial_pass=commercial_pass,
        overall_pass=overall_pass,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def get_excel_formulas(refs: dict) -> dict:
    """Checks are computed, not formula-based."""
    return {}
