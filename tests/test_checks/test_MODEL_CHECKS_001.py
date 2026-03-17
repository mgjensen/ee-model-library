"""Tests for MODEL_CHECKS_001 — automated model integrity and commercial checks."""

import math
import pytest
from modules.checks.MODEL_CHECKS_001 import (
    CheckResult,
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _balanced_inputs(periods=60, start_year=2025, start_month=1, tolerance=1.0):
    """Build a fully balanced model that passes all checks."""
    n = periods
    assets = [1000.0] * n
    liab_eq = [1000.0] * n
    cash = [50.0] * n
    debt = [500.0] * n
    revenue = [100.0] * n
    opex = [30.0] * n
    dep = [20.0] * n
    interest = [10.0] * n
    tax = [10.0] * n
    ni = [30.0] * n  # 100 - 30 - 20 - 10 - 10 = 30
    capex_m = [10.0] * n
    cum_capex = [10.0 * (p + 1) for p in range(n)]
    cum_dep = [5.0 * (p + 1) for p in range(n)]  # always below cum_capex
    dscr = [1.50] * n
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        tolerance=tolerance,
        total_assets=assets,
        total_liabilities_equity=liab_eq,
        closing_cash=cash,
        debt_closing_balance=debt,
        dscr_series=dscr,
        dscr_covenant=1.10,
        gross_revenue=revenue,
        total_opex=opex,
        depreciation=dep,
        interest_expense=interest,
        tax_charge=tax,
        net_income=ni,
        capex_monthly=capex_m,
        cumulative_capex=cum_capex,
        total_capex_budget=700.0,
        cumulative_depreciation=cum_dep,
        sources_uses_balance=0.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_balanced_model_all_pass():
    inp = _balanced_inputs()
    out = calculate(inp)
    assert out.overall_pass is True
    assert out.integrity_pass is True
    assert out.commercial_pass is True
    assert all(c.passed for c in out.checks)


def test_bs_imbalance_fails():
    inp = _balanced_inputs()
    inp.total_assets[10] = 2000.0  # big gap
    out = calculate(inp)
    bs_check = next(c for c in out.checks if c.name == "Balance Sheet Alignment")
    assert bs_check.passed is False
    assert out.integrity_pass is False
    assert out.overall_pass is False


def test_negative_cash_fails():
    inp = _balanced_inputs()
    inp.closing_cash[5] = -50.0
    out = calculate(inp)
    cash_check = next(c for c in out.checks if c.name == "Negative Cash Balance")
    assert cash_check.passed is False
    assert cash_check.max_deviation == 50.0


def test_dscr_breach_fails():
    inp = _balanced_inputs()
    inp.dscr_series[20] = 0.95
    out = calculate(inp)
    dscr_check = next(c for c in out.checks if c.name == "DSCR Covenant")
    assert dscr_check.passed is False
    assert out.commercial_pass is False
    assert out.overall_pass is False


def test_is_consistency_fails():
    inp = _balanced_inputs()
    # Break net_income so it doesn't match revenue - costs
    inp.net_income[0] = 999.0
    out = calculate(inp)
    is_check = next(c for c in out.checks if c.name == "Income Statement Consistency")
    assert is_check.passed is False
    assert is_check.max_deviation > 1.0


def test_over_depreciation_fails():
    inp = _balanced_inputs()
    # Make cum depreciation exceed cum capex
    inp.cumulative_depreciation = [1000.0 * (p + 1) for p in range(inp.periods)]
    out = calculate(inp)
    dep_check = next(c for c in out.checks if c.name == "Depreciation vs Capex")
    assert dep_check.passed is False


def test_su_imbalance_fails():
    inp = _balanced_inputs()
    inp.sources_uses_balance = 50.0
    out = calculate(inp)
    su_check = next(c for c in out.checks if c.name == "Sources & Uses Balanced")
    assert su_check.passed is False


def test_negative_debt_fails():
    inp = _balanced_inputs()
    inp.debt_closing_balance[3] = -10.0
    out = calculate(inp)
    debt_check = next(c for c in out.checks if c.name == "No Negative Debt Balance")
    assert debt_check.passed is False
    assert debt_check.max_deviation == 10.0


def test_configurable_tolerance():
    """A large tolerance allows deviations that would otherwise fail."""
    inp = _balanced_inputs(tolerance=100.0)
    inp.total_assets[10] = 1050.0  # 50 DKKk deviation, within 100 tol
    inp.closing_cash[5] = -50.0   # within -100 tol
    inp.sources_uses_balance = 80.0  # within 100 tol
    out = calculate(inp)
    bs_check = next(c for c in out.checks if c.name == "Balance Sheet Alignment")
    cash_check = next(c for c in out.checks if c.name == "Negative Cash Balance")
    su_check = next(c for c in out.checks if c.name == "Sources & Uses Balanced")
    assert bs_check.passed is True
    assert cash_check.passed is True
    assert su_check.passed is True


def test_no_dscr_data_passes():
    inp = _balanced_inputs()
    inp.dscr_series = []
    out = calculate(inp)
    dscr_check = next(c for c in out.checks if c.name == "DSCR Covenant")
    assert dscr_check.passed is True
    assert "no data" in dscr_check.detail.lower()


def test_output_structure():
    inp = _balanced_inputs()
    out = calculate(inp)
    assert isinstance(out, Outputs)
    assert isinstance(out.checks, list)
    assert all(isinstance(c, CheckResult) for c in out.checks)
    assert isinstance(out.integrity_pass, bool)
    assert isinstance(out.commercial_pass, bool)
    assert isinstance(out.overall_pass, bool)
    assert isinstance(out.summary, str)
    assert len(out.checks) == 8  # all 8 checks present


def test_summary_string_ok():
    inp = _balanced_inputs()
    out = calculate(inp)
    assert out.summary == "OK"


def test_summary_string_fail():
    inp = _balanced_inputs()
    inp.closing_cash[0] = -100.0
    out = calculate(inp)
    assert out.summary.startswith("FAIL: ")
    assert "Negative Cash Balance" in out.summary


def test_categories_correct():
    inp = _balanced_inputs()
    out = calculate(inp)
    for c in out.checks:
        assert c.category in ("integrity", "commercial")
    commercial_names = {c.name for c in out.checks if c.category == "commercial"}
    assert "DSCR Covenant" in commercial_names
    integrity_names = {c.name for c in out.checks if c.category == "integrity"}
    assert "Balance Sheet Alignment" in integrity_names
    assert "Sources & Uses Balanced" in integrity_names


def test_multiple_failures():
    inp = _balanced_inputs()
    inp.total_assets[0] = 9999.0       # BS fail
    inp.closing_cash[0] = -500.0       # cash fail
    inp.dscr_series[0] = 0.5           # DSCR fail
    inp.sources_uses_balance = 999.0   # S&U fail
    out = calculate(inp)
    failed = [c.name for c in out.checks if not c.passed]
    assert len(failed) >= 4
    assert out.overall_pass is False
    assert out.integrity_pass is False
    assert out.commercial_pass is False
    # Summary should list all failed names
    for name in failed:
        assert name in out.summary
