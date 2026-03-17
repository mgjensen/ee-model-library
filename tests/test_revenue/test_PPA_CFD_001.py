"""Tests for PPA_CFD_001 — Baseload PPA / Contract for Difference."""

import calendar
import pytest
from modules.revenue.PPA_CFD_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _year_groups,
    _escalation_factor,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make(
    periods=12,
    start_year=2025,
    start_month=1,
    active=True,
    volume_MW=50.0,
    strike=100.0,
    wholesale=80.0,
    start_period=0,
    end_period=None,
    esc_rate=0.0,
    esc_start=None,
    hours_per_month=None,
) -> Inputs:
    n = periods
    kw = {}
    if hours_per_month is not None:
        kw["hours_per_month"] = hours_per_month
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        active=active,
        volume_MW=volume_MW,
        strike_price_per_MWh=strike,
        wholesale_price_monthly=[wholesale] * n,
        start_period=start_period,
        end_period=end_period,
        strike_escalation_rate=esc_rate,
        escalation_start_year=esc_start,
        **kw,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_inactive_returns_zeros():
    inp = _make(active=False)
    out = calculate(inp)
    assert all(v == 0.0 for v in out.settlement_value)
    assert out.total_settlement_lifetime == 0.0
    assert all(v == 0.0 for v in out.cfd_flag)


def test_positive_settlement():
    """Strike > wholesale => positive settlement."""
    inp = _make(strike=120.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    out = calculate(inp)
    # (120 - 80) * 50 * 100 / 1000 = 200 DKKk per month
    for v in out.settlement_value:
        assert v == pytest.approx(200.0)
    assert out.total_positive_settlement == pytest.approx(200.0 * 12)
    assert out.total_negative_settlement == pytest.approx(0.0)


def test_negative_settlement():
    """Strike < wholesale => negative settlement."""
    inp = _make(strike=60.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    out = calculate(inp)
    # (60 - 80) * 50 * 100 / 1000 = -100 DKKk per month
    for v in out.settlement_value:
        assert v == pytest.approx(-100.0)
    assert out.total_negative_settlement == pytest.approx(-100.0 * 12)
    assert out.total_positive_settlement == pytest.approx(0.0)


def test_volume_times_hours():
    """Settlement volume = volume_MW * hours."""
    inp = _make(volume_MW=10.0, hours_per_month=[200.0] * 12)
    out = calculate(inp)
    for v in out.settlement_volume_MWh:
        assert v == pytest.approx(10.0 * 200.0)


def test_timing_flags():
    """CfD flag only active in [start_period, end_period)."""
    inp = _make(periods=12, start_period=3, end_period=8)
    out = calculate(inp)
    for p in range(12):
        if 3 <= p < 8:
            assert out.cfd_flag[p] == 1.0
        else:
            assert out.cfd_flag[p] == 0.0
            assert out.settlement_value[p] == 0.0


def test_escalation_compounds():
    """Strike escalates annually from escalation_start_year."""
    inp = _make(
        periods=24,
        start_year=2025,
        start_month=1,
        strike=100.0,
        esc_rate=0.05,
        esc_start=2025,
        hours_per_month=[100.0] * 24,
    )
    out = calculate(inp)
    # Year 2025 (p 0-11): factor = 1.0, strike = 100
    assert out.strike_price_indexed[0] == pytest.approx(100.0)
    # Year 2026 (p 12-23): factor = 1.05, strike = 105
    assert out.strike_price_indexed[12] == pytest.approx(105.0)


def test_auto_calc_hours():
    """Without hours_per_month, hours derived from calendar."""
    inp = _make(periods=3, start_year=2025, start_month=1, strike=100.0, wholesale=0.0)
    out = calculate(inp)
    # Jan 2025: 31 days * 24 = 744 hours
    expected_jan = 50.0 * 744.0
    assert out.settlement_volume_MWh[0] == pytest.approx(expected_jan)
    # Feb 2025: 28 days * 24 = 672 hours
    expected_feb = 50.0 * 672.0
    assert out.settlement_volume_MWh[1] == pytest.approx(expected_feb)
    # Mar 2025: 31 days * 24 = 744 hours
    expected_mar = 50.0 * 744.0
    assert out.settlement_volume_MWh[2] == pytest.approx(expected_mar)


def test_hours_override():
    """Explicit hours_per_month overrides calendar calculation."""
    override = [100.0] * 12
    inp = _make(hours_per_month=override)
    out = calculate(inp)
    for p in range(12):
        assert out.settlement_volume_MWh[p] == pytest.approx(50.0 * 100.0)


def test_lifetime_total():
    inp = _make(strike=100.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    out = calculate(inp)
    assert out.total_settlement_lifetime == pytest.approx(sum(out.settlement_value))


def test_annual_aggregation():
    """Annual settlement sums months within each calendar year."""
    inp = _make(
        periods=24, start_year=2025, start_month=7,
        strike=100.0, wholesale=80.0, hours_per_month=[100.0] * 24,
    )
    out = calculate(inp)
    # Year 2025: periods 0-5 (Jul-Dec), Year 2026: periods 6-17 (Jan-Dec), Year 2027: 18-23 (Jan-Jun)
    per_month = (100.0 - 80.0) * 50.0 * 100.0 / 1000.0  # = 100 DKKk
    assert out.annual_settlement[0] == pytest.approx(per_month * 6)   # 2025
    assert out.annual_settlement[1] == pytest.approx(per_month * 12)  # 2026
    assert out.annual_settlement[2] == pytest.approx(per_month * 6)   # 2027


def test_mixed_positive_negative():
    """Different wholesale prices per period produce mix of +/- settlements."""
    prices = [80.0, 120.0, 80.0, 120.0] * 3  # 12 periods alternating
    inp = Inputs(
        periods=12,
        start_year=2025,
        start_month=1,
        active=True,
        volume_MW=10.0,
        strike_price_per_MWh=100.0,
        wholesale_price_monthly=prices,
        hours_per_month=[100.0] * 12,
    )
    out = calculate(inp)
    assert out.total_positive_settlement > 0.0
    assert out.total_negative_settlement < 0.0
    assert out.total_settlement_lifetime == pytest.approx(
        out.total_positive_settlement + out.total_negative_settlement
    )


def test_zero_spread():
    """Strike == wholesale => zero settlement."""
    inp = _make(strike=80.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    out = calculate(inp)
    for v in out.settlement_value:
        assert v == pytest.approx(0.0)
    assert out.total_settlement_lifetime == pytest.approx(0.0)


def test_output_lengths():
    inp = _make(periods=36)
    out = calculate(inp)
    assert len(out.cfd_flag) == 36
    assert len(out.strike_price_indexed) == 36
    assert len(out.wholesale_price) == 36
    assert len(out.settlement_per_MWh) == 36
    assert len(out.settlement_volume_MWh) == 36
    assert len(out.settlement_value) == 36
    # Annual: 36 months starting Jan 2025 = 3 calendar years
    assert len(out.annual_settlement) == 3


def test_large_volume_scales():
    """Large MW volume scales settlement proportionally."""
    inp_small = _make(volume_MW=10.0, strike=100.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    inp_large = _make(volume_MW=100.0, strike=100.0, wholesale=80.0, hours_per_month=[100.0] * 12)
    out_s = calculate(inp_small)
    out_l = calculate(inp_large)
    assert out_l.total_settlement_lifetime == pytest.approx(
        out_s.total_settlement_lifetime * 10.0
    )


def test_before_start_zero():
    """Periods before start_period have zero settlement."""
    inp = _make(periods=12, start_period=6, hours_per_month=[100.0] * 12)
    out = calculate(inp)
    for p in range(6):
        assert out.cfd_flag[p] == 0.0
        assert out.settlement_value[p] == 0.0
        assert out.settlement_volume_MWh[p] == 0.0
    for p in range(6, 12):
        assert out.cfd_flag[p] == 1.0
        assert out.settlement_value[p] != 0.0
