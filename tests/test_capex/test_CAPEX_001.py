"""Tests for CAPEX_001 — capital expenditure schedule module."""

import pytest
from modules.capex.CAPEX_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=36,
    construction_start_period=0,
    construction_periods=12,
    drawdown_profile=None,
    epc_DKKk=100_000.0,
    grid_connection_DKKk=10_000.0,
    development_DKKk=5_000.0,
    land_DKKk=3_000.0,
    contingency_pct=0.05,
    other_DKKk=2_000.0,
) -> Inputs:
    if drawdown_profile is None:
        drawdown_profile = [1.0 / construction_periods] * construction_periods
    return Inputs(
        periods=periods,
        construction_start_period=construction_start_period,
        construction_periods=construction_periods,
        drawdown_profile=drawdown_profile,
        epc_DKKk=epc_DKKk,
        grid_connection_DKKk=grid_connection_DKKk,
        development_DKKk=development_DKKk,
        land_DKKk=land_DKKk,
        contingency_pct=contingency_pct,
        other_DKKk=other_DKKk,
    )


# ---------------------------------------------------------------------------
# Unit: Inputs validation
# ---------------------------------------------------------------------------

def test_drawdown_profile_length_mismatch():
    with pytest.raises(ValueError, match="drawdown_profile length"):
        Inputs(
            periods=24,
            construction_start_period=0,
            construction_periods=6,
            drawdown_profile=[0.5, 0.5],  # wrong length
            epc_DKKk=100_000.0,
        )


def test_drawdown_profile_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1.0"):
        Inputs(
            periods=24,
            construction_start_period=0,
            construction_periods=3,
            drawdown_profile=[0.3, 0.3, 0.3],  # sums to 0.9
            epc_DKKk=100_000.0,
        )


def test_construction_window_exceeds_periods():
    with pytest.raises(ValueError, match="extends beyond"):
        Inputs(
            periods=10,
            construction_start_period=5,
            construction_periods=6,  # 5+6=11 > 10
            drawdown_profile=[1/6] * 6,
            epc_DKKk=100_000.0,
        )


# ---------------------------------------------------------------------------
# Unit: calculate — basic structure
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_calculate_array_lengths():
    out = calculate(_base_inputs(periods=36))
    assert len(out.epc) == 36
    assert len(out.grid_connection) == 36
    assert len(out.development) == 36
    assert len(out.land) == 36
    assert len(out.contingency) == 36
    assert len(out.other) == 36
    assert len(out.total_capex_monthly) == 36
    assert len(out.cumulative_capex) == 36


# ---------------------------------------------------------------------------
# Unit: spend distribution
# ---------------------------------------------------------------------------

def test_uniform_profile_equal_monthly_spend():
    """Uniform drawdown → equal spend each construction month."""
    out = calculate(_base_inputs(
        construction_start_period=0,
        construction_periods=12,
        epc_DKKk=12_000.0,
        grid_connection_DKKk=0.0,
        development_DKKk=0.0,
        land_DKKk=0.0,
        contingency_pct=0.0,
        other_DKKk=0.0,
    ))
    for p in range(12):
        assert out.epc[p] == pytest.approx(1_000.0)


def test_spend_zero_outside_construction():
    """No spend before or after construction window."""
    out = calculate(_base_inputs(
        periods=36,
        construction_start_period=6,
        construction_periods=12,
    ))
    for p in range(6):
        assert out.total_capex_monthly[p] == pytest.approx(0.0)
    for p in range(18, 36):
        assert out.total_capex_monthly[p] == pytest.approx(0.0)


def test_spend_within_construction_window():
    out = calculate(_base_inputs(
        periods=36,
        construction_start_period=6,
        construction_periods=12,
    ))
    # All spend is in periods 6–17
    assert sum(out.total_capex_monthly[6:18]) == pytest.approx(sum(out.total_capex_monthly))


def test_front_loaded_profile():
    """50/30/20 front-loaded profile."""
    out = calculate(_base_inputs(
        construction_periods=3,
        drawdown_profile=[0.5, 0.3, 0.2],
        epc_DKKk=100_000.0,
        grid_connection_DKKk=0.0,
        development_DKKk=0.0,
        land_DKKk=0.0,
        contingency_pct=0.0,
        other_DKKk=0.0,
    ))
    assert out.epc[0] == pytest.approx(50_000.0)
    assert out.epc[1] == pytest.approx(30_000.0)
    assert out.epc[2] == pytest.approx(20_000.0)


def test_back_loaded_profile():
    out = calculate(_base_inputs(
        construction_periods=3,
        drawdown_profile=[0.2, 0.3, 0.5],
        epc_DKKk=100_000.0,
        grid_connection_DKKk=0.0,
        development_DKKk=0.0,
        land_DKKk=0.0,
        contingency_pct=0.0,
        other_DKKk=0.0,
    ))
    assert out.epc[2] > out.epc[0]


# ---------------------------------------------------------------------------
# Unit: contingency
# ---------------------------------------------------------------------------

def test_contingency_is_pct_of_base():
    out = calculate(_base_inputs(
        epc_DKKk=100_000.0,
        grid_connection_DKKk=10_000.0,
        development_DKKk=5_000.0,
        land_DKKk=5_000.0,
        contingency_pct=0.05,
        other_DKKk=0.0,
    ))
    # base = 120,000, contingency = 6,000
    assert out.total_contingency == pytest.approx(6_000.0)
    assert sum(out.contingency) == pytest.approx(6_000.0)


def test_contingency_excludes_other():
    """Other costs are not included in contingency base."""
    out = calculate(_base_inputs(
        epc_DKKk=100_000.0,
        grid_connection_DKKk=0.0,
        development_DKKk=0.0,
        land_DKKk=0.0,
        contingency_pct=0.10,
        other_DKKk=50_000.0,  # should not attract contingency
    ))
    assert out.total_contingency == pytest.approx(10_000.0)


def test_zero_contingency():
    out = calculate(_base_inputs(contingency_pct=0.0))
    assert all(v == pytest.approx(0.0) for v in out.contingency)


# ---------------------------------------------------------------------------
# Unit: scalar totals
# ---------------------------------------------------------------------------

def test_total_epc_passthrough():
    out = calculate(_base_inputs(epc_DKKk=123_456.0))
    assert out.total_epc == pytest.approx(123_456.0)


def test_total_grid_passthrough():
    out = calculate(_base_inputs(grid_connection_DKKk=8_000.0))
    assert out.total_grid_connection == pytest.approx(8_000.0)


def test_total_monthly_sums_to_total_capex():
    out = calculate(_base_inputs())
    assert sum(out.total_capex_monthly) == pytest.approx(out.total_capex)


def test_total_capex_equals_sum_of_components():
    out = calculate(_base_inputs())
    expected = (
        out.total_epc + out.total_grid_connection + out.total_development
        + out.total_land + out.total_contingency + out.total_other
    )
    assert out.total_capex == pytest.approx(expected)


def test_monthly_total_is_sum_of_categories():
    out = calculate(_base_inputs())
    for p in range(36):
        expected = (
            out.epc[p] + out.grid_connection[p] + out.development[p]
            + out.land[p] + out.contingency[p] + out.other[p]
        )
        assert out.total_capex_monthly[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Unit: cumulative capex
# ---------------------------------------------------------------------------

def test_cumulative_capex_is_running_total():
    out = calculate(_base_inputs())
    running = 0.0
    for p in range(36):
        running += out.total_capex_monthly[p]
        assert out.cumulative_capex[p] == pytest.approx(running)


def test_cumulative_capex_final_equals_total():
    out = calculate(_base_inputs())
    assert out.cumulative_capex[-1] == pytest.approx(out.total_capex)


def test_cumulative_capex_non_decreasing():
    out = calculate(_base_inputs())
    for p in range(1, 36):
        assert out.cumulative_capex[p] >= out.cumulative_capex[p - 1] - 1e-9


def test_cumulative_capex_zero_before_construction():
    out = calculate(_base_inputs(construction_start_period=6))
    for p in range(6):
        assert out.cumulative_capex[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration: Viuf proxy (258 MWp PV + 25 MW BESS, 18-month build)
# ---------------------------------------------------------------------------

def test_viuf_proxy_capex():
    """
    Viuf-scale project: ~750 MKK total (PV 258 MWp + BESS).
    18-month construction, S-curve drawdown.
    """
    construction_periods = 18
    # S-curve: slow start, peak mid, tail off
    s_curve = [
        0.02, 0.04, 0.06, 0.08, 0.10,
        0.10, 0.10, 0.08, 0.07, 0.07,
        0.06, 0.05, 0.04, 0.04, 0.03,
        0.03, 0.02, 0.01,
    ]
    assert abs(sum(s_curve) - 1.0) < 1e-9

    out = calculate(Inputs(
        periods=475,
        construction_start_period=0,
        construction_periods=construction_periods,
        drawdown_profile=s_curve,
        epc_DKKk=550_000.0,
        grid_connection_DKKk=80_000.0,
        development_DKKk=30_000.0,
        land_DKKk=20_000.0,
        contingency_pct=0.05,
        other_DKKk=25_000.0,
    ))

    assert 600_000 < out.total_capex < 900_000  # 600-900 MKK plausible
    assert len(out.epc) == 475
    # Peak spend in middle of construction
    mid = construction_periods // 2
    assert out.total_capex_monthly[mid] > out.total_capex_monthly[0]


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "total_cost": "B10", "drawdown_fraction": "K$5", "contingency_pct": "B15",
        "epc": "K20", "grid": "K21", "dev": "K22", "land": "K23",
        "other": "K25", "prev_cumulative": "J30", "total_capex_monthly": "K30",
    }
    formulas = get_excel_formulas(refs)
    for key in ("monthly_spend", "contingency", "total_capex_monthly", "cumulative_capex"):
        assert key in formulas
        assert formulas[key].startswith("=")
