"""Tests for DIV_001 — dividend distribution + capital reduction module."""

import pytest
from modules.statements.DIV_001 import Inputs, Outputs, calculate


def _base_inputs(n=60, cod=12, **overrides):
    """Base test config: 5-year model, COD at month 12, semi-annual payments."""
    defaults = dict(
        periods=n, start_year=2026, start_month=1,
        cod_period=cod,
        net_income=[100.0] * n,
        closing_cash=[5000.0] * n,
        opening_retained_earnings=0.0,
        opening_contributed_equity=10000.0,
        payout_ratio=0.80,
        cash_reserve=40.0,
        payment_months=[6, 12],
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ============================================================================
# GATE 1: Operations flag
# ============================================================================

def test_no_dividend_before_cod():
    out = calculate(_base_inputs(cod=12))
    assert all(d == 0 for d in out.dividends_paid[:12])


def test_dividends_start_after_cod():
    out = calculate(_base_inputs(cod=0))
    # First payment month after COD: June (p=5)
    assert out.dividends_paid[5] > 0


def test_no_dividend_after_ops_end():
    out = calculate(_base_inputs(n=60, cod=0, ops_end_period=30))
    assert all(d == 0 for d in out.dividends_paid[31:])


# ============================================================================
# GATE 2: Retained earnings must be positive
# ============================================================================

def test_no_dividend_when_re_negative():
    out = calculate(_base_inputs(
        cod=0, net_income=[-100.0] * 60,
        opening_retained_earnings=0.0,
    ))
    assert all(d == 0 for d in out.dividends_paid)


def test_dividend_when_re_positive():
    out = calculate(_base_inputs(cod=0))
    assert out.total_dividends > 0


def test_re_tracks_cumulative_net_income():
    out = calculate(_base_inputs(cod=0, net_income=[50.0] * 60))
    # RE at p0 = opening(0) + NI(50) = 50
    assert abs(out.retained_earnings[0] - 50.0) < 0.01


# ============================================================================
# GATE 3: Cash above reserve
# ============================================================================

def test_no_dividend_when_cash_below_reserve():
    out = calculate(_base_inputs(
        cod=0, closing_cash=[30.0] * 60, cash_reserve=40.0,
    ))
    assert all(d == 0 for d in out.dividends_paid)


def test_dividend_limited_by_cash():
    # Cash = 100, reserve = 40 → available = 60. RE = huge → div = 60 * 0.80 = 48
    out = calculate(_base_inputs(
        cod=0, closing_cash=[100.0] * 60,
        opening_retained_earnings=100000.0,
    ))
    first_pay = next(d for d in out.dividends_paid if d > 0)
    assert abs(first_pay - 48.0) < 0.01


def test_dividend_limited_by_re():
    # RE = 10 (small), cash = 5000 → div = min(10, 4960) * 0.80 = 8
    out = calculate(_base_inputs(
        cod=0, net_income=[10.0] * 60,
        opening_retained_earnings=0.0,
    ))
    # At first payment month (p=5): RE = 6*10 = 60, cash = 5000
    # div = min(60, 4960) * 0.80 = 48
    assert out.dividends_paid[5] > 0
    assert out.dividends_paid[5] <= 60 * 0.80 + 0.01


# ============================================================================
# PAYOUT RATIO
# ============================================================================

def test_payout_ratio_applied():
    out = calculate(_base_inputs(cod=0, payout_ratio=0.50))
    out_80 = calculate(_base_inputs(cod=0, payout_ratio=0.80))
    # Same inputs, lower payout → lower dividends
    assert out.total_dividends < out_80.total_dividends


def test_payout_ratio_zero_no_dividends():
    out = calculate(_base_inputs(cod=0, payout_ratio=0.0))
    assert out.total_dividends == 0


# ============================================================================
# PAYMENT FREQUENCY
# ============================================================================

def test_payment_only_in_specified_months():
    out = calculate(_base_inputs(cod=0, payment_months=[6, 12]))
    for p in range(60):
        cal_month = (p % 12) + 1
        if cal_month not in {6, 12}:
            assert out.dividends_paid[p] == 0


def test_annual_payment():
    out = calculate(_base_inputs(cod=0, payment_months=[12]))
    pay_periods = [p for p in range(60) if out.dividends_paid[p] > 0]
    # Only December (p=11, 23, 35, 47, 59)
    assert all((p % 12) + 1 == 12 for p in pay_periods)


# ============================================================================
# CAPITAL REDUCTION
# ============================================================================

def test_capital_reduction_inactive_by_default():
    out = calculate(_base_inputs(cod=0))
    assert all(c == 0 for c in out.capital_reduction)


def test_capital_reduction_returns_equity():
    out = calculate(_base_inputs(
        cod=0, capital_reduction_active=True,
        capital_reduction_threshold=100.0,
        opening_contributed_equity=10000.0,
        closing_cash=[5000.0] * 60,
    ))
    assert out.total_capital_reduction > 0


def test_capital_reduction_capped_at_equity():
    out = calculate(_base_inputs(
        cod=0, capital_reduction_active=True,
        capital_reduction_threshold=100.0,
        opening_contributed_equity=100.0,  # small equity
        closing_cash=[50000.0] * 60,  # lots of cash
    ))
    assert out.total_capital_reduction <= 100.0 + 0.01


# ============================================================================
# OUTPUT STRUCTURE
# ============================================================================

def test_output_lengths():
    out = calculate(_base_inputs())
    assert len(out.dividends_paid) == 60
    assert len(out.capital_reduction) == 60
    assert len(out.total_distribution) == 60
    assert len(out.retained_earnings) == 60
    assert len(out.distributable_cash) == 60


def test_total_distribution_is_sum():
    out = calculate(_base_inputs(
        cod=0, capital_reduction_active=True,
        capital_reduction_threshold=100.0,
    ))
    for p in range(60):
        assert abs(out.total_distribution[p] - out.dividends_paid[p] - out.capital_reduction[p]) < 0.01


def test_distribution_periods_count():
    out = calculate(_base_inputs(cod=0))
    expected = sum(1 for v in out.total_distribution if v > 0)
    assert out.distribution_periods == expected


# ============================================================================
# BACKWARD COMPATIBILITY
# ============================================================================

def test_all_defaults_zero_dividends():
    """With default payout_ratio and no cash, no dividends."""
    out = calculate(_base_inputs(
        cod=0, closing_cash=[0.0] * 60, cash_reserve=40.0,
    ))
    assert out.total_dividends == 0


def test_validation_wrong_length():
    with pytest.raises(ValueError, match="net_income length"):
        _base_inputs(net_income=[100.0] * 10)  # wrong length


def test_validation_bad_payment_month():
    with pytest.raises(ValueError, match="payment_months must be 1-12"):
        _base_inputs(payment_months=[0, 13])


def test_excel_formulas_keys():
    from modules.statements.DIV_001 import get_excel_formulas
    refs = {
        "ops_flag": "A1", "retained_earnings": "B1", "cash": "C1",
        "reserve": "D1", "payout_ratio": "E1", "prev_re": "F1",
        "net_income": "G1", "dividends": "H1", "cap_reduction": "I1",
    }
    formulas = get_excel_formulas(refs)
    assert "dividends_paid" in formulas
    assert "retained_earnings" in formulas
