"""Tests for WORKING_CAPITAL_001 — working capital module."""

import pytest
from modules.statements.WORKING_CAPITAL_001 import (
    Inputs,
    Outputs,
    RevenueStream,
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
    streams=None,
    opex=None,
    payable_days=30,
):
    if streams is None:
        streams = []
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        revenue_streams=streams,
        total_opex_monthly=opex if opex is not None else [],
        payable_days=payable_days,
    )


def _flat_stream(name, value, periods, receivable_days=30):
    return RevenueStream(
        name=name,
        revenue_monthly=[value] * periods,
        receivable_days=receivable_days,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_30_day_delay():
    """30-day receivable → 1-month lag; receivable equals one month of revenue."""
    n = 12
    rev = 100.0
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", rev, n, receivable_days=30)],
    )
    out = calculate(inp)
    # After ramp-up (p >= 1), receivable stays at one month of revenue
    for p in range(1, n):
        assert out.total_receivables[p] == pytest.approx(rev, abs=1e-9)
    # Cash received from month 1 onward equals revenue
    for p in range(1, n):
        assert out.total_revenue_received[p] == pytest.approx(rev, abs=1e-9)


def test_60_day_delay():
    """60-day receivable → 2-month lag; receivable builds to 2× monthly revenue."""
    n = 12
    rev = 50.0
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", rev, n, receivable_days=60)],
    )
    out = calculate(inp)
    # After 2-month ramp, receivable = 2 months of revenue
    for p in range(2, n):
        assert out.total_receivables[p] == pytest.approx(2 * rev, abs=1e-9)
    # No cash received in first 2 months
    assert out.total_revenue_received[0] == 0.0
    assert out.total_revenue_received[1] == 0.0
    # From month 2 onward, cash = revenue
    for p in range(2, n):
        assert out.total_revenue_received[p] == pytest.approx(rev, abs=1e-9)


def test_zero_day_treated_as_one_month():
    """receivable_days=0 still results in a 1-month lag (min delay)."""
    n = 6
    rev = 200.0
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", rev, n, receivable_days=0)],
    )
    out = calculate(inp)
    # First period: no cash received
    assert out.total_revenue_received[0] == 0.0
    # From period 1, cash received = revenue
    for p in range(1, n):
        assert out.total_revenue_received[p] == pytest.approx(rev, abs=1e-9)


def test_balance_non_negative():
    """With positive revenue and no opex, receivables >= 0 and payables == 0."""
    n = 12
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", 100.0, n)],
    )
    out = calculate(inp)
    for p in range(n):
        assert out.total_receivables[p] >= 0.0
        assert out.total_payables[p] == 0.0


def test_wc_change_sums_to_final_balance():
    """Sum of all wc_change entries equals the final wc_balance."""
    n = 24
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", 80.0, n, receivable_days=45)],
        opex=[20.0] * n,
        payable_days=30,
    )
    out = calculate(inp)
    assert sum(out.working_capital_change) == pytest.approx(
        out.working_capital_balance[-1], abs=1e-9
    )


def test_multiple_streams():
    """Two streams with different delays accumulate independently."""
    n = 12
    s1 = _flat_stream("pv_merchant", 100.0, n, receivable_days=30)
    s2 = _flat_stream("pv_ppa", 50.0, n, receivable_days=60)
    inp = _make_inputs(periods=n, streams=[s1, s2])
    out = calculate(inp)
    # After ramp: receivable = 1×100 + 2×50 = 200
    for p in range(2, n):
        assert out.total_receivables[p] == pytest.approx(200.0, abs=1e-9)


def test_revenue_received_is_revenue_lagged():
    """total_revenue_received[p] == revenue[p - delay] for each stream."""
    n = 6
    rev = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    stream = RevenueStream(name="test", revenue_monthly=rev, receivable_days=30)
    inp = _make_inputs(periods=n, streams=[stream])
    out = calculate(inp)
    assert out.total_revenue_received[0] == 0.0
    for p in range(1, n):
        assert out.total_revenue_received[p] == pytest.approx(rev[p - 1], abs=1e-9)


def test_opex_paid_lagged():
    """opex_paid[p] == opex[p - delay] with 30-day payable."""
    n = 6
    opex = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    inp = _make_inputs(periods=n, opex=opex, payable_days=30)
    out = calculate(inp)
    assert out.opex_paid[0] == 0.0
    for p in range(1, n):
        assert out.opex_paid[p] == pytest.approx(opex[p - 1], abs=1e-9)


def test_zero_revenue_all_zeros():
    """Zero revenue streams produce all-zero receivables and revenue received."""
    n = 12
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", 0.0, n)],
    )
    out = calculate(inp)
    assert all(v == 0.0 for v in out.total_receivables)
    assert all(v == 0.0 for v in out.total_revenue_received)


def test_output_lengths():
    """All output arrays have length == periods."""
    n = 36
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", 100.0, n)],
        opex=[10.0] * n,
    )
    out = calculate(inp)
    assert isinstance(out, Outputs)
    for field_name in [
        "total_receivables",
        "total_payables",
        "working_capital_balance",
        "working_capital_change",
        "total_revenue_received",
        "opex_paid",
    ]:
        assert len(getattr(out, field_name)) == n


def test_first_months_no_cash_received():
    """During the delay window no cash is collected."""
    n = 12
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("bess", 100.0, n, receivable_days=90)],
    )
    out = calculate(inp)
    delay = 3  # round(90/30) = 3
    for p in range(delay):
        assert out.total_revenue_received[p] == 0.0


def test_steady_state_change_near_zero():
    """After ramp-up with constant revenue, wc_change → 0."""
    n = 24
    inp = _make_inputs(
        periods=n,
        streams=[_flat_stream("pv", 100.0, n, receivable_days=30)],
        opex=[20.0] * n,
        payable_days=30,
    )
    out = calculate(inp)
    # After month 1, both delays have settled; wc_change should be zero
    for p in range(2, n):
        assert out.working_capital_change[p] == pytest.approx(0.0, abs=1e-9)


def test_seasonal_volatility():
    """Varying revenue produces non-zero wc_change after ramp-up."""
    n = 12
    rev = [100.0 if p % 2 == 0 else 50.0 for p in range(n)]
    stream = RevenueStream(name="seasonal", revenue_monthly=rev, receivable_days=30)
    inp = _make_inputs(periods=n, streams=[stream])
    out = calculate(inp)
    # After month 0, wc_change should not all be zero due to varying revenue
    changes_after_ramp = out.working_capital_change[2:]
    assert any(abs(c) > 1e-9 for c in changes_after_ramp)


def test_validation_wrong_length():
    """Mismatched revenue_monthly length raises ValueError."""
    with pytest.raises(ValueError, match="revenue_streams"):
        Inputs(
            periods=12,
            start_year=2025,
            start_month=1,
            revenue_streams=[
                RevenueStream(name="bad", revenue_monthly=[100.0] * 6)
            ],
        )


def test_no_streams_returns_zeros():
    """No revenue streams and no opex → all outputs are zero."""
    n = 12
    inp = _make_inputs(periods=n)
    out = calculate(inp)
    for field_name in [
        "total_receivables",
        "total_payables",
        "working_capital_balance",
        "working_capital_change",
        "total_revenue_received",
        "opex_paid",
    ]:
        assert all(v == 0.0 for v in getattr(out, field_name))
