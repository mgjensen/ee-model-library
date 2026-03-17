"""Tests for SHL_001 — shareholder loan module."""

import pytest
from modules.debt.SHL_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=120,
    start_year=2025,
    start_month=1,
    shl_pct=0.80,
    margin=0.0795,
    accrued=True,
    equity_total=100_000.0,
    n_draw=12,
    repayment_schedule=None,
):
    """Standard SHL inputs with equity drawn evenly over n_draw months."""
    eq = [equity_total / n_draw] * n_draw + [0.0] * (periods - n_draw)
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        shl_pct_of_equity=shl_pct,
        margin=margin,
        accrued=accrued,
        equity_contributed=eq,
        repayment_schedule=repayment_schedule,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_equity_contributed_length_mismatch():
    with pytest.raises(ValueError, match="equity_contributed"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            equity_contributed=[100.0] * 6,  # wrong length
        )


def test_repayment_schedule_length_mismatch():
    with pytest.raises(ValueError, match="repayment_schedule"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            equity_contributed=[100.0] * 12,
            repayment_schedule=[0.0] * 6,  # wrong length
        )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 120
    out = calculate(_make_inputs(periods=n))
    for field in ("opening_balance", "interest", "repayment",
                  "closing_balance", "interest_cash", "total_cash_flow"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# SHL sizing
# ---------------------------------------------------------------------------

def test_initial_shl_is_pct_of_equity():
    out = calculate(_make_inputs(equity_total=200_000.0, shl_pct=0.80))
    assert out.initial_shl == pytest.approx(160_000.0)


def test_initial_shl_zero_equity():
    out = calculate(_make_inputs(equity_total=0.0, shl_pct=0.80))
    assert out.initial_shl == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in out.opening_balance)


def test_initial_shl_full_equity():
    out = calculate(_make_inputs(equity_total=100_000.0, shl_pct=1.0))
    assert out.initial_shl == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# Balance mechanics — accrued (PIK)
# ---------------------------------------------------------------------------

def test_accrued_interest_increases_balance():
    """With PIK, closing balance should exceed opening after interest."""
    out = calculate(_make_inputs(
        periods=24, n_draw=6, accrued=True, margin=0.10,
    ))
    # After drawdowns complete (period 6), balance should grow due to interest
    assert out.closing_balance[12] > out.opening_balance[12]


def test_accrued_interest_cash_is_zero():
    """PIK mode: no cash interest paid."""
    out = calculate(_make_inputs(accrued=True))
    assert all(v == pytest.approx(0.0) for v in out.interest_cash)


def test_accrued_closing_equals_opening_plus_interest_minus_repayment():
    out = calculate(_make_inputs(periods=24, n_draw=6, accrued=True))
    for p in range(24):
        expected = out.opening_balance[p] + out.interest[p] - out.repayment[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


def test_accrued_balance_compounds():
    """PIK balance should compound: closing > initial_shl after some periods."""
    out = calculate(_make_inputs(
        periods=60, n_draw=6, accrued=True, margin=0.10,
        equity_total=100_000.0, shl_pct=1.0,
    ))
    # After 60 months of compounding at 10%, balance should exceed initial
    # (but final period repays everything, so check period 58)
    assert out.opening_balance[58] > 100_000.0


# ---------------------------------------------------------------------------
# Balance mechanics — cash-pay
# ---------------------------------------------------------------------------

def test_cash_pay_interest_paid_each_period():
    """Cash-pay mode: interest_cash = interest."""
    out = calculate(_make_inputs(accrued=False))
    for p in range(len(out.interest)):
        assert out.interest_cash[p] == pytest.approx(out.interest[p])


def test_cash_pay_balance_flat_after_drawdown():
    """Cash-pay: balance stays constant after last drawdown (no compounding)."""
    out = calculate(_make_inputs(
        periods=60, n_draw=6, accrued=False,
        equity_total=100_000.0, shl_pct=1.0,
    ))
    # Between last drawdown and final repayment, balance = initial_shl
    for p in range(6, 59):
        assert out.closing_balance[p] == pytest.approx(100_000.0, abs=0.01)


def test_cash_pay_closing_equals_opening_minus_repayment():
    out = calculate(_make_inputs(periods=24, n_draw=6, accrued=False))
    for p in range(24):
        expected = out.opening_balance[p] - out.repayment[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Default repayment (final period bullet)
# ---------------------------------------------------------------------------

def test_default_repayment_in_final_period():
    """No repayment_schedule → full balance repaid in last period."""
    out = calculate(_make_inputs(periods=60, n_draw=6))
    # All repayment in period 59
    assert out.repayment[59] > 0
    assert all(out.repayment[p] == pytest.approx(0.0) for p in range(59))


def test_default_repayment_fully_repaid():
    out = calculate(_make_inputs(periods=60, n_draw=6))
    assert out.fully_repaid
    assert out.final_balance == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Custom repayment schedule
# ---------------------------------------------------------------------------

def test_custom_repayment_schedule():
    n = 24
    n_draw = 6
    equity_total = 100_000.0
    shl_pct = 1.0
    # Repay 10,000 per month from period 12 to 21 (10 months)
    sched = [0.0] * 12 + [10_000.0] * 10 + [0.0] * 2
    out = calculate(_make_inputs(
        periods=n, n_draw=n_draw, accrued=False, margin=0.0,
        equity_total=equity_total, shl_pct=shl_pct,
        repayment_schedule=sched,
    ))
    assert out.total_repayment == pytest.approx(100_000.0)
    assert out.final_balance == pytest.approx(0.0, abs=0.01)


def test_repayment_capped_at_balance():
    """Repayment can't exceed outstanding balance."""
    n = 12
    sched = [0.0] * 6 + [999_999.0] * 6  # way more than balance
    out = calculate(_make_inputs(
        periods=n, n_draw=6, accrued=False, margin=0.0,
        equity_total=10_000.0, shl_pct=1.0,
        repayment_schedule=sched,
    ))
    assert out.total_repayment == pytest.approx(10_000.0, abs=0.01)
    assert all(b >= -1e-6 for b in out.closing_balance)


# ---------------------------------------------------------------------------
# Total cash flow
# ---------------------------------------------------------------------------

def test_total_cash_flow_accrued():
    """Accrued: total_cash = repayment only (no interest cash)."""
    out = calculate(_make_inputs(accrued=True))
    for p in range(len(out.total_cash_flow)):
        assert out.total_cash_flow[p] == pytest.approx(out.repayment[p])


def test_total_cash_flow_cash_pay():
    """Cash-pay: total_cash = repayment + interest."""
    out = calculate(_make_inputs(accrued=False))
    for p in range(len(out.total_cash_flow)):
        expected = out.repayment[p] + out.interest_cash[p]
        assert out.total_cash_flow[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Interest calculation
# ---------------------------------------------------------------------------

def test_interest_on_opening_balance():
    out = calculate(_make_inputs(margin=0.12, periods=24, n_draw=6))
    r = 0.12 / 12.0
    for p in range(24):
        assert out.interest[p] == pytest.approx(out.opening_balance[p] * r, abs=1e-6)


def test_zero_margin_no_interest():
    out = calculate(_make_inputs(margin=0.0))
    assert all(v == pytest.approx(0.0) for v in out.interest)
    assert out.total_interest == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Balance never negative
# ---------------------------------------------------------------------------

def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)


def test_repayment_never_negative():
    out = calculate(_make_inputs())
    assert all(r >= -1e-9 for r in out.repayment)


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "closing_prev": "K9", "shl_drawdown": "K10",
        "opening_bal": "K11", "monthly_margin": "B5",
        "interest": "K12", "repayment": "K13",
    }
    formulas = get_excel_formulas(refs)
    for key in ("opening_balance", "interest",
                "closing_balance_accrued", "closing_balance_cash"):
        assert key in formulas
        assert formulas[key].startswith("=")
