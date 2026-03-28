"""Tests for CASH_SWEEP_001 — excess cash sweep module."""

import pytest
from modules.debt.CASH_SWEEP_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    active=True,
    cash_available=None,
    sweep_pct=0.50,
    sweep_start_period=0,
    sweep_end_period=None,
    suspension_start_period=None,
    suspension_end_period=None,
    debt_opening_balance=None,
):
    if cash_available is None:
        cash_available = [1000.0] * periods
    if debt_opening_balance is None:
        debt_opening_balance = [50_000.0] * periods
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        active=active,
        cash_available=cash_available,
        sweep_pct=sweep_pct,
        sweep_start_period=sweep_start_period,
        sweep_end_period=sweep_end_period,
        suspension_start_period=suspension_start_period,
        suspension_end_period=suspension_end_period,
        debt_opening_balance=debt_opening_balance,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_basic_50pct_sweep():
    """50% sweep of 1,000 available = 500 swept per period."""
    out = calculate(_make_inputs(sweep_pct=0.50))
    assert out.cash_swept[0] == pytest.approx(500.0)
    assert out.cash_swept[10] == pytest.approx(500.0)
    assert out.cash_after_sweep[0] == pytest.approx(500.0)


def test_inactive_zeros():
    """When active=False, no cash is swept."""
    out = calculate(_make_inputs(active=False))
    assert all(v == pytest.approx(0.0) for v in out.cash_swept)
    assert all(v == pytest.approx(0.0) for v in out.sweep_active_flag)
    assert out.total_swept == pytest.approx(0.0)


def test_sweep_capped_at_debt():
    """Swept amount cannot exceed debt opening balance."""
    out = calculate(_make_inputs(
        cash_available=[10_000.0] * 24,
        sweep_pct=1.0,
        debt_opening_balance=[500.0] * 24,
    ))
    for p in range(24):
        assert out.cash_swept[p] == pytest.approx(500.0)


def test_suspension_pauses():
    """Sweep pauses during suspension window."""
    out = calculate(_make_inputs(
        suspension_start_period=5,
        suspension_end_period=10,
    ))
    for p in range(5, 10):
        assert out.cash_swept[p] == pytest.approx(0.0)
        assert out.sweep_active_flag[p] == pytest.approx(0.0)
    # Before and after suspension, sweep is active
    assert out.cash_swept[4] == pytest.approx(500.0)
    assert out.cash_swept[10] == pytest.approx(500.0)


def test_no_negative_sweep():
    """Negative cash_available results in zero sweep, not negative."""
    out = calculate(_make_inputs(
        cash_available=[-500.0] * 24,
    ))
    assert all(v == pytest.approx(0.0) for v in out.cash_swept)


def test_sweep_start_respected():
    """No sweep before sweep_start_period."""
    out = calculate(_make_inputs(sweep_start_period=12))
    for p in range(12):
        assert out.cash_swept[p] == pytest.approx(0.0)
        assert out.sweep_active_flag[p] == pytest.approx(0.0)
    assert out.cash_swept[12] == pytest.approx(500.0)


def test_sweep_end_respected():
    """No sweep at or after sweep_end_period."""
    out = calculate(_make_inputs(sweep_end_period=6))
    for p in range(6):
        assert out.cash_swept[p] == pytest.approx(500.0)
    for p in range(6, 24):
        assert out.cash_swept[p] == pytest.approx(0.0)


def test_cumulative_sum():
    """Cumulative swept should be running sum of cash_swept."""
    out = calculate(_make_inputs(periods=12, sweep_pct=0.50))
    running = 0.0
    for p in range(12):
        running += out.cash_swept[p]
        assert out.cumulative_swept[p] == pytest.approx(running)


def test_sweep_active_flag():
    """Flag is 1.0 when sweep is active, 0.0 otherwise."""
    out = calculate(_make_inputs(
        sweep_start_period=3,
        sweep_end_period=10,
    ))
    for p in range(3):
        assert out.sweep_active_flag[p] == pytest.approx(0.0)
    for p in range(3, 10):
        assert out.sweep_active_flag[p] == pytest.approx(1.0)
    for p in range(10, 24):
        assert out.sweep_active_flag[p] == pytest.approx(0.0)


def test_zero_cash_no_sweep():
    """Zero cash available → zero swept."""
    out = calculate(_make_inputs(
        cash_available=[0.0] * 24,
    ))
    assert all(v == pytest.approx(0.0) for v in out.cash_swept)
    assert out.total_swept == pytest.approx(0.0)


def test_validation_wrong_length():
    """cash_available or debt_opening_balance wrong length raises ValueError."""
    with pytest.raises(ValueError, match="cash_available length"):
        _make_inputs(cash_available=[100.0] * 10, periods=24)

    with pytest.raises(ValueError, match="debt_opening_balance length"):
        _make_inputs(debt_opening_balance=[100.0] * 10, periods=24)


def test_total_swept_scalar():
    """total_swept equals sum of cash_swept."""
    out = calculate(_make_inputs(periods=12))
    assert out.total_swept == pytest.approx(sum(out.cash_swept))


def test_output_lengths():
    """All output arrays have length == periods."""
    n = 36
    out = calculate(_make_inputs(periods=n))
    assert len(out.cash_swept) == n
    assert len(out.cash_after_sweep) == n
    assert len(out.cumulative_swept) == n
    assert len(out.sweep_active_flag) == n


def test_debt_zero_no_sweep():
    """When debt balance is zero, no sweep even if cash available."""
    out = calculate(_make_inputs(
        debt_opening_balance=[0.0] * 24,
        cash_available=[1000.0] * 24,
    ))
    assert all(v == pytest.approx(0.0) for v in out.cash_swept)
    assert all(v == pytest.approx(0.0) for v in out.sweep_active_flag)


def test_excel_formulas():
    """get_excel_formulas returns dict with expected keys and formula strings."""
    refs = {
        "cash_available": "K20",
        "sweep_pct": "B5",
        "debt_bal": "K10",
        "sweep_flag": "K30",
        "cash_swept": "K31",
        "cumulative_prev": "J32",
    }
    formulas = get_excel_formulas(refs)
    for key in ("cash_swept", "cash_after_sweep", "cumulative_swept"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ============================================================================
# DSCR THRESHOLD GATING (Session 3 — UC Model Learnings)
# ============================================================================

def _make_threshold_inputs(dscr_values, threshold=1.15):
    """Build inputs with DSCR threshold gating."""
    n = len(dscr_values)
    return Inputs(
        periods=n, start_year=2026, start_month=1,
        active=True,
        cash_available=[100.0] * n,
        sweep_pct=0.50,
        debt_opening_balance=[1000.0] * n,
        dscr_threshold=threshold,
        dscr_monthly=dscr_values,
    )


def test_dscr_threshold_low_triggers_sweep():
    """DSCR 1.10 < threshold 1.15 → sweep fires."""
    out = calculate(_make_threshold_inputs([1.10] * 12))
    assert all(s > 0 for s in out.cash_swept)


def test_dscr_threshold_high_skips_sweep():
    """DSCR 1.30 >= threshold 1.15 → sweep skipped."""
    out = calculate(_make_threshold_inputs([1.30] * 12))
    assert all(s == 0 for s in out.cash_swept)


def test_dscr_threshold_mixed():
    """Sweep fires only in periods where DSCR < threshold."""
    dscr = [1.10, 1.30, 1.05, 1.20, 1.14, 1.16]
    out = calculate(_make_threshold_inputs(dscr))
    # Periods 0, 2, 4: DSCR < 1.15 → sweep
    # Periods 1, 3, 5: DSCR >= 1.15 → no sweep
    assert out.cash_swept[0] > 0
    assert out.cash_swept[1] == 0
    assert out.cash_swept[2] > 0
    assert out.cash_swept[3] == 0
    assert out.cash_swept[4] > 0
    assert out.cash_swept[5] == 0


def test_dscr_threshold_none_sweeps_unconditionally():
    """No threshold → current behavior (sweep every active period)."""
    n = 12
    inp = Inputs(
        periods=n, start_year=2026, start_month=1,
        active=True,
        cash_available=[100.0] * n,
        sweep_pct=0.50,
        debt_opening_balance=[1000.0] * n,
        dscr_threshold=None,
        dscr_monthly=None,
    )
    out = calculate(inp)
    assert all(s > 0 for s in out.cash_swept)


def test_dscr_threshold_validation_missing_array():
    """dscr_threshold set without dscr_monthly → ValueError."""
    with pytest.raises(ValueError, match="dscr_monthly must be provided"):
        Inputs(
            periods=12, start_year=2026, start_month=1,
            active=True,
            cash_available=[100.0] * 12,
            sweep_pct=0.50,
            debt_opening_balance=[1000.0] * 12,
            dscr_threshold=1.15,
            dscr_monthly=None,
        )


def test_dscr_threshold_validation_wrong_length():
    """dscr_monthly wrong length → ValueError."""
    with pytest.raises(ValueError, match="dscr_monthly length"):
        Inputs(
            periods=12, start_year=2026, start_month=1,
            active=True,
            cash_available=[100.0] * 12,
            sweep_pct=0.50,
            debt_opening_balance=[1000.0] * 12,
            dscr_threshold=1.15,
            dscr_monthly=[1.10] * 10,
        )
