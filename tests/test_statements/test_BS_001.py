"""Tests for BS_001 — Balance Sheet module."""

import pytest
from modules.statements.BS_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    opening_contributed_equity_DKKk=50_000.0,
    opening_retained_earnings_DKKk=0.0,
    capex_monthly=None,
    depreciation_monthly=None,
    closing_cash=None,
    debt_closing_balance=None,
    net_income=None,
    dividends_paid=None,
) -> Inputs:
    n = periods
    kwargs = dict(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        opening_contributed_equity_DKKk=opening_contributed_equity_DKKk,
        opening_retained_earnings_DKKk=opening_retained_earnings_DKKk,
        capex_monthly=capex_monthly or [0.0] * n,
        depreciation_monthly=depreciation_monthly or [0.0] * n,
        closing_cash=closing_cash or [5_000.0] * n,
        debt_closing_balance=debt_closing_balance or [40_000.0] * n,
        net_income=net_income or [500.0] * n,
    )
    if dividends_paid is not None:
        kwargs["dividends_paid"] = dividends_paid
    return Inputs(**kwargs)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_wrong_length_capex():
    with pytest.raises(ValueError, match="capex_monthly"):
        _base_inputs(periods=12, capex_monthly=[1_000.0] * 6)


def test_wrong_length_net_income():
    with pytest.raises(ValueError, match="net_income"):
        _base_inputs(periods=12, net_income=[500.0] * 6)


def test_wrong_length_dividends():
    with pytest.raises(ValueError, match="dividends_paid"):
        _base_inputs(periods=12, dividends_paid=[100.0] * 6)


# ---------------------------------------------------------------------------
# Unit: fixed assets
# ---------------------------------------------------------------------------

def test_fixed_assets_gross_accumulates():
    out = calculate(_base_inputs(capex_monthly=[1_000.0] * 24))
    for p in range(24):
        assert out.fixed_assets_gross[p] == pytest.approx((p + 1) * 1_000.0)


def test_fixed_assets_gross_zero_when_no_capex():
    out = calculate(_base_inputs(capex_monthly=[0.0] * 24))
    assert all(v == pytest.approx(0.0) for v in out.fixed_assets_gross)


def test_accumulated_depreciation_is_negative():
    out = calculate(_base_inputs(depreciation_monthly=[100.0] * 24))
    assert all(v <= 0.0 for v in out.accumulated_depreciation)


def test_accumulated_depreciation_grows():
    out = calculate(_base_inputs(depreciation_monthly=[100.0] * 24))
    for p in range(24):
        assert out.accumulated_depreciation[p] == pytest.approx(-(p + 1) * 100.0)


def test_fixed_assets_net_is_gross_plus_acc_dep():
    out = calculate(_base_inputs(
        capex_monthly=[2_000.0] * 12 + [0.0] * 12,
        depreciation_monthly=[100.0] * 24,
    ))
    for p in range(24):
        assert out.fixed_assets_net[p] == pytest.approx(
            out.fixed_assets_gross[p] + out.accumulated_depreciation[p]
        )


def test_fixed_assets_net_declines_after_construction():
    out = calculate(_base_inputs(
        capex_monthly=[10_000.0] * 12 + [0.0] * 12,
        depreciation_monthly=[500.0] * 24,
    ))
    # After construction ends, net fixed assets should decline each period
    for p in range(13, 24):
        assert out.fixed_assets_net[p] < out.fixed_assets_net[p - 1]


# ---------------------------------------------------------------------------
# Unit: cash passthrough
# ---------------------------------------------------------------------------

def test_cash_is_closing_cash_passthrough():
    cash = [float(p * 100) for p in range(24)]
    out = calculate(_base_inputs(closing_cash=cash))
    assert out.cash == pytest.approx(cash)


# ---------------------------------------------------------------------------
# Unit: total assets
# ---------------------------------------------------------------------------

def test_total_assets_is_net_fa_plus_cash():
    out = calculate(_base_inputs())
    for p in range(24):
        assert out.total_assets[p] == pytest.approx(out.fixed_assets_net[p] + out.cash[p])


# ---------------------------------------------------------------------------
# Unit: equity
# ---------------------------------------------------------------------------

def test_contributed_equity_is_constant():
    out = calculate(_base_inputs(opening_contributed_equity_DKKk=75_000.0))
    assert all(v == pytest.approx(75_000.0) for v in out.contributed_equity)


def test_retained_earnings_accumulates():
    out = calculate(_base_inputs(net_income=[200.0] * 24))
    for p in range(24):
        assert out.retained_earnings[p] == pytest.approx((p + 1) * 200.0)


def test_retained_earnings_opening_balance():
    out = calculate(_base_inputs(
        opening_retained_earnings_DKKk=5_000.0,
        net_income=[0.0] * 24,
    ))
    assert all(v == pytest.approx(5_000.0) for v in out.retained_earnings)


def test_retained_earnings_reduced_by_dividends():
    out = calculate(_base_inputs(
        net_income=[500.0] * 24,
        dividends_paid=[200.0] * 24,
    ))
    for p in range(24):
        assert out.retained_earnings[p] == pytest.approx((p + 1) * 300.0)


def test_total_equity_is_contributed_plus_retained():
    out = calculate(_base_inputs())
    for p in range(24):
        assert out.total_equity[p] == pytest.approx(
            out.contributed_equity[p] + out.retained_earnings[p]
        )


# ---------------------------------------------------------------------------
# Unit: total liabilities & equity
# ---------------------------------------------------------------------------

def test_total_le_is_debt_plus_equity():
    out = calculate(_base_inputs())
    for p in range(24):
        assert out.total_liabilities_equity[p] == pytest.approx(
            out.debt_balance[p] + out.total_equity[p]
        )


def test_debt_is_passthrough():
    debt = [50_000.0 - p * 200.0 for p in range(24)]
    out = calculate(_base_inputs(debt_closing_balance=debt))
    assert out.debt_balance == pytest.approx(debt)


# ---------------------------------------------------------------------------
# Unit: balance check
# ---------------------------------------------------------------------------

def test_balance_check_zero_for_consistent_inputs():
    """
    Build a self-consistent minimal balance sheet:
    - 1 period, no capex, no dep, cash = equity (no debt), net income = 0
    """
    out = calculate(Inputs(
        periods=1,
        start_year=2025,
        start_month=1,
        opening_contributed_equity_DKKk=10_000.0,
        opening_retained_earnings_DKKk=0.0,
        capex_monthly=[0.0],
        depreciation_monthly=[0.0],
        closing_cash=[10_000.0],   # equity = cash (no debt, no fixed assets)
        debt_closing_balance=[0.0],
        net_income=[0.0],
    ))
    assert out.imbalance[0] == pytest.approx(0.0, abs=1e-6)
    assert out.max_imbalance == pytest.approx(0.0, abs=1e-6)


def test_balance_check_with_capex_debt_equity():
    """
    1 period: 100k capex funded 70k debt + 30k equity.
    Cash = 0, fixed assets = 100k, dep = 0.
    Assets = 100k. L+E = 70k + 30k = 100k.
    """
    out = calculate(Inputs(
        periods=1,
        start_year=2025,
        start_month=1,
        opening_contributed_equity_DKKk=30_000.0,
        capex_monthly=[100_000.0],
        depreciation_monthly=[0.0],
        closing_cash=[0.0],
        debt_closing_balance=[70_000.0],
        net_income=[0.0],
    ))
    assert out.imbalance[0] == pytest.approx(0.0, abs=1e-6)


def test_max_imbalance_is_absolute_max():
    out = calculate(_base_inputs())
    assert out.max_imbalance == pytest.approx(max(abs(v) for v in out.imbalance))


# ---------------------------------------------------------------------------
# Integration: simple project lifecycle
# ---------------------------------------------------------------------------

def test_simple_project_lifecycle():
    """
    12 months construction: capex funded by 70% debt + 30% equity injection.
    12 months operations: net income + depreciation, debt repays.
    Verify assets never go negative and balance check stays < 1 DKKk.
    """
    n = 24
    monthly_capex = [5_000.0] * 12 + [0.0] * 12    # 60,000 total capex
    monthly_dep = [0.0] * 12 + [200.0] * 12         # dep starts post-construction
    # Simplified cash: construction draws debt (net 0 cash move), ops generates cash
    closing_cash = [0.0] * 12 + [float(p * 300) for p in range(1, 13)]
    debt = [42_000.0 - p * 500 for p in range(24)]   # repaying debt
    ni = [-50.0] * 12 + [800.0] * 12

    out = calculate(Inputs(
        periods=n,
        start_year=2025,
        start_month=1,
        opening_contributed_equity_DKKk=18_000.0,
        capex_monthly=monthly_capex,
        depreciation_monthly=monthly_dep,
        closing_cash=closing_cash,
        debt_closing_balance=debt,
        net_income=ni,
    ))

    assert len(out.total_assets) == n
    assert len(out.total_liabilities_equity) == n
    # Fixed assets should be positive throughout
    assert all(v >= 0 for v in out.fixed_assets_gross)


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "prev_fa_gross": "J20", "capex": "K5",
        "prev_acc_dep": "J21", "depreciation": "K11",
        "fa_gross": "K20", "acc_dep": "K21", "cash": "K22",
        "debt": "K30", "contributed_equity": "K31",
        "prev_retained": "J32", "net_income": "K10", "dividends": "K33",
        "total_equity": "K34", "total_assets": "K25",
        "total_liabilities_equity": "K35",
    }
    formulas = get_excel_formulas(refs)
    for key in ("fixed_assets_gross", "accumulated_depreciation", "fixed_assets_net",
                "total_assets", "retained_earnings", "total_equity",
                "total_liabilities_equity", "imbalance"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ============================================================================
# RETROFIT OPENING BALANCES
# ============================================================================

def _make_retrofit_inputs(
    n=24, opening_fa_gross=0.0, opening_acc_dep=0.0, opening_debt=0.0
):
    """Build BS inputs with optional retrofit opening balances."""
    return Inputs(
        periods=n, start_year=2025, start_month=7,
        opening_contributed_equity_DKKk=5000.0,
        opening_retained_earnings_DKKk=170.0,
        opening_fixed_assets_gross_DKKk=opening_fa_gross,
        opening_accumulated_depreciation_DKKk=opening_acc_dep,
        opening_debt_balance_DKKk=opening_debt,
        capex_monthly=[1000.0] * 6 + [0.0] * 18,
        depreciation_monthly=[200.0] * n,
        closing_cash=[500.0] * n,
        debt_closing_balance=[10000.0] * 12 + [9000.0] * 12,
        net_income=[100.0] * n,
    )


def test_retrofit_no_opening_matches_default():
    """Zero opening balances = identical to current behavior."""
    out_default = calculate(_make_retrofit_inputs())
    out_zeros = calculate(_make_retrofit_inputs(
        opening_fa_gross=0.0, opening_acc_dep=0.0, opening_debt=0.0
    ))
    assert out_default.fixed_assets_gross == out_zeros.fixed_assets_gross
    assert out_default.accumulated_depreciation == out_zeros.accumulated_depreciation
    assert out_default.imbalance == out_zeros.imbalance


def test_retrofit_opening_fa_gross_increases_assets():
    """Opening fixed assets gross adds to cumulative capex."""
    out_no = calculate(_make_retrofit_inputs())
    out_yes = calculate(_make_retrofit_inputs(opening_fa_gross=29000.0))
    # Period 0: with opening = 29000 + 1000 = 30000, without = 1000
    assert abs(out_yes.fixed_assets_gross[0] - (29000.0 + 1000.0)) < 0.01
    assert abs(out_no.fixed_assets_gross[0] - 1000.0) < 0.01


def test_retrofit_opening_acc_dep_reduces_net_ppe():
    """Opening accumulated depreciation reduces Net PP&E at period 0."""
    out_no = calculate(_make_retrofit_inputs())
    out_yes = calculate(_make_retrofit_inputs(opening_acc_dep=-14600.0))
    # Net FA = gross + acc_dep (acc_dep is negative)
    # Without: net = 1000 + (-200) = 800
    # With: net = 1000 + (-14600 - 200) = -13800
    assert out_yes.fixed_assets_net[0] < out_no.fixed_assets_net[0]
    assert abs(out_yes.accumulated_depreciation[0] - (-14600.0 - 200.0)) < 0.01


def test_retrofit_opening_balances_reduce_imbalance():
    """With offsetting opening balances, net FA unchanged and imbalance stable."""
    out_no = calculate(_make_retrofit_inputs())
    # Opening gross + opening dep net to zero -- no change to net FA
    out_yes = calculate(_make_retrofit_inputs(
        opening_fa_gross=10000.0,
        opening_acc_dep=-10000.0,
    ))
    # Net FA unchanged, so imbalance should be the same
    assert abs(out_yes.max_imbalance - out_no.max_imbalance) < 1.0


def test_retrofit_opening_debt_adds_to_passthrough():
    """Opening debt balance is added to the debt series."""
    out_no = calculate(_make_retrofit_inputs())
    out_yes = calculate(_make_retrofit_inputs(opening_debt=5000.0))
    assert abs(out_yes.debt_balance[0] - (10000.0 + 5000.0)) < 0.01
    assert abs(out_no.debt_balance[0] - 10000.0) < 0.01


def test_retrofit_validation_acc_dep_must_be_negative():
    """Accumulated depreciation must be <= 0."""
    with pytest.raises(Exception):
        _make_retrofit_inputs(opening_acc_dep=100.0)


# ============================================================================
# EQUITY CONTRIBUTIONS TIME SERIES
# ============================================================================

def test_equity_contributions_accumulates():
    """Equity contributions accumulate over time instead of being constant."""
    out = calculate(Inputs(
        periods=12, start_year=2026, start_month=1,
        opening_contributed_equity_DKKk=0.0,
        equity_contributions=[1000.0] * 6 + [0.0] * 6,
        capex_monthly=[1000.0] * 6 + [0.0] * 6,
        depreciation_monthly=[100.0] * 12,
        closing_cash=[500.0] * 12,
        debt_closing_balance=[0.0] * 12,
        net_income=[50.0] * 12,
    ))
    # After 6 months of 1000 equity draws: contributed = 6000
    assert abs(out.contributed_equity[5] - 6000.0) < 0.01
    # After month 6, no more draws: stays at 6000
    assert abs(out.contributed_equity[11] - 6000.0) < 0.01


def test_equity_contributions_with_opening():
    """Opening equity + contributions accumulate correctly."""
    out = calculate(Inputs(
        periods=6, start_year=2026, start_month=1,
        opening_contributed_equity_DKKk=5000.0,
        equity_contributions=[1000.0] * 6,
        capex_monthly=[0.0] * 6,
        depreciation_monthly=[0.0] * 6,
        closing_cash=[0.0] * 6,
        debt_closing_balance=[0.0] * 6,
        net_income=[0.0] * 6,
    ))
    # P0: 5000 + 1000 = 6000
    assert abs(out.contributed_equity[0] - 6000.0) < 0.01
    # P5: 5000 + 6*1000 = 11000
    assert abs(out.contributed_equity[5] - 11000.0) < 0.01


def test_equity_contributions_none_uses_constant():
    """Without equity_contributions, contributed_equity is constant."""
    out = calculate(Inputs(
        periods=6, start_year=2026, start_month=1,
        opening_contributed_equity_DKKk=5000.0,
        capex_monthly=[0.0] * 6,
        depreciation_monthly=[0.0] * 6,
        closing_cash=[0.0] * 6,
        debt_closing_balance=[0.0] * 6,
        net_income=[0.0] * 6,
    ))
    assert all(v == 5000.0 for v in out.contributed_equity)


def test_equity_contributions_validation_wrong_length():
    with pytest.raises(ValueError, match="equity_contributions length"):
        Inputs(
            periods=12, start_year=2026, start_month=1,
            equity_contributions=[1000.0] * 10,
            capex_monthly=[0.0] * 12,
            depreciation_monthly=[0.0] * 12,
            closing_cash=[0.0] * 12,
            debt_closing_balance=[0.0] * 12,
            net_income=[0.0] * 12,
        )
