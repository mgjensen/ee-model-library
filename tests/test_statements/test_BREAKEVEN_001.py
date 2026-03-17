"""Tests for BREAKEVEN_001 — breakeven price analysis module."""

import pytest
from modules.statements.BREAKEVEN_001 import Inputs, Outputs, calculate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(periods=120, production=100.0, opex=50.0, ds=20.0, tax=10.0):
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        production_MWh=[production] * periods,
        total_opex_monthly=[opex] * periods,
        debt_service_monthly=[ds] * periods,
        tax_monthly=[tax] * periods,
    )


# ---------------------------------------------------------------------------
# Basic calculations
# ---------------------------------------------------------------------------

def test_breakeven_basic():
    """Total cost = (50+20+10) DKKk, prod = 100 MWh → 800 DKK/MWh."""
    out = calculate(_make_inputs())
    # (50+20+10)*1000/100 = 800 DKK/MWh
    assert out.breakeven_price_monthly[0] == pytest.approx(800.0)


def test_zero_production_zero_breakeven():
    """Zero production should yield zero breakeven (not division error)."""
    out = calculate(_make_inputs(production=0.0))
    assert all(b == 0.0 for b in out.breakeven_price_monthly)


def test_zero_costs_zero_breakeven():
    """Zero costs should yield zero breakeven price."""
    out = calculate(_make_inputs(opex=0.0, ds=0.0, tax=0.0))
    assert out.average_breakeven == pytest.approx(0.0)


def test_higher_opex_higher_breakeven():
    """Higher OPEX should produce higher breakeven price."""
    low = calculate(_make_inputs(opex=30.0))
    high = calculate(_make_inputs(opex=80.0))
    assert high.average_breakeven > low.average_breakeven


def test_max_breakeven():
    """max_breakeven should equal the maximum of the monthly series."""
    out = calculate(_make_inputs())
    assert out.max_breakeven == pytest.approx(max(out.breakeven_price_monthly))


def test_output_lengths():
    """Output arrays should match the requested period count."""
    out = calculate(_make_inputs(periods=60))
    assert len(out.breakeven_price_monthly) == 60


# ---------------------------------------------------------------------------
# Averages
# ---------------------------------------------------------------------------

def test_average_breakeven_equals_lifetime():
    """average_breakeven and average_over_lifetime should be the same."""
    out = calculate(_make_inputs())
    assert out.average_breakeven == pytest.approx(out.average_over_lifetime)


def test_average_over_debt_tenor():
    """With debt_tenor_end_period set, tenor average uses only those periods."""
    inp = Inputs(
        periods=120,
        start_year=2026,
        start_month=1,
        production_MWh=[100.0] * 120,
        total_opex_monthly=[50.0] * 60 + [100.0] * 60,
        debt_service_monthly=[20.0] * 120,
        tax_monthly=[10.0] * 120,
        debt_tenor_end_period=60,
    )
    out = calculate(inp)
    # Tenor covers first 60 periods: cost = (50+20+10)*1000/100 = 800
    assert out.average_over_debt_tenor == pytest.approx(800.0)
    # Lifetime includes higher OPEX second half
    assert out.average_over_lifetime > out.average_over_debt_tenor


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_production_length_mismatch():
    with pytest.raises(ValueError, match="production_MWh"):
        Inputs(
            periods=10,
            start_year=2026,
            start_month=1,
            production_MWh=[100.0] * 5,
            total_opex_monthly=[50.0] * 10,
        )


def test_opex_length_mismatch():
    with pytest.raises(ValueError, match="total_opex_monthly"):
        Inputs(
            periods=10,
            start_year=2026,
            start_month=1,
            production_MWh=[100.0] * 10,
            total_opex_monthly=[50.0] * 5,
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_returns_outputs_instance():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_single_period():
    out = calculate(_make_inputs(periods=1))
    assert len(out.breakeven_price_monthly) == 1
    assert out.breakeven_price_monthly[0] == pytest.approx(800.0)


def test_higher_debt_service_higher_breakeven():
    low = calculate(_make_inputs(ds=10.0))
    high = calculate(_make_inputs(ds=50.0))
    assert high.average_breakeven > low.average_breakeven


def test_higher_production_lower_breakeven():
    low_prod = calculate(_make_inputs(production=50.0))
    high_prod = calculate(_make_inputs(production=200.0))
    assert high_prod.average_breakeven < low_prod.average_breakeven
