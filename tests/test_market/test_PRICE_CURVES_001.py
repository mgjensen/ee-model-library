"""Tests for PRICE_CURVES_001 — market price curve library."""

import pytest
from modules.market.PRICE_CURVES_001 import (
    Inputs,
    Outputs,
    PriceCurve,
    CurveLibrary,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_curve(name="test", category="price", unit="DKK/MWh",
                frequency="monthly", start_year=2025, start_month=1,
                values=None, source=""):
    return PriceCurve(
        name=name, category=category, unit=unit,
        frequency=frequency, source=source,
        start_year=start_year, start_month=start_month,
        values=values or [],
    )


def _make_library(curves_dict=None):
    if curves_dict is None:
        curves_dict = {}
    return CurveLibrary(curves={k: v for k, v in curves_dict.items()})


def _make_inputs(periods=24, start_year=2025, start_month=1, library=None, **kwargs):
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        library=library or CurveLibrary(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_library_returns_zeros():
    """No curves in library, no selections — all outputs are zeros."""
    out = calculate(_make_inputs(periods=12))
    assert out.capture_price_monthly == [0.0] * 12
    assert out.bess_revenue_monthly == [0.0] * 12
    assert out.wholesale_price_monthly == [0.0] * 12
    assert out.interest_rate_monthly == [0.0] * 12
    assert out.goo_price_monthly == [0.0] * 12
    assert out.curtailment_monthly == [0.0] * 12
    assert out.imbalance_cost_monthly == [0.0] * 12
    assert out.bess_opex_monthly == [0.0] * 12
    assert out.missing_curves == []


def test_curve_alignment_exact_match():
    """Curve starts at same date as project — values pass through directly."""
    vals = [10.0 * (i + 1) for i in range(24)]
    lib = _make_library({
        "cap": _make_curve(name="cap", values=vals, start_year=2025, start_month=1),
    })
    inp = _make_inputs(periods=24, start_year=2025, start_month=1,
                       library=lib, capture_price_curve="cap")
    out = calculate(inp)
    assert out.capture_price_monthly == vals


def test_curve_alignment_offset():
    """Curve starts before project — values are offset correctly."""
    # Curve starts 2025-01, project starts 2025-07 → skip first 6 values
    vals = list(range(36))  # 0..35
    lib = _make_library({
        "ws": _make_curve(name="ws", values=vals, start_year=2025, start_month=1),
    })
    inp = _make_inputs(periods=12, start_year=2025, start_month=7,
                       library=lib, wholesale_curve="ws")
    out = calculate(inp)
    assert out.wholesale_price_monthly == list(range(6, 18))


def test_curve_padding_short():
    """Curve shorter than periods — last value is extrapolated."""
    vals = [100.0, 200.0, 300.0]
    lib = _make_library({
        "short": _make_curve(name="short", values=vals, start_year=2025, start_month=1),
    })
    inp = _make_inputs(periods=6, start_year=2025, start_month=1,
                       library=lib, capture_price_curve="short")
    out = calculate(inp)
    assert out.capture_price_monthly == [100.0, 200.0, 300.0, 300.0, 300.0, 300.0]


def test_annual_to_monthly_spread():
    """Annual curve values are repeated 12 times each."""
    vals = [50.0, 60.0, 70.0]  # 3 years
    lib = _make_library({
        "ann": _make_curve(name="ann", values=vals, frequency="annual",
                           start_year=2025, start_month=1),
    })
    # Project starts Jan 2025, 24 periods = 2 years
    inp = _make_inputs(periods=24, start_year=2025, start_month=1,
                       library=lib, wholesale_curve="ann")
    out = calculate(inp)
    assert out.wholesale_price_monthly[:12] == [50.0] * 12
    assert out.wholesale_price_monthly[12:24] == [60.0] * 12


def test_inflation_compounding():
    """Inflation index compounds: 1.0 in year 1, 1.02 in year 2, ~1.0404 in year 3."""
    # Constant 2% annual inflation rate curve
    vals = [0.02] * 36  # monthly values (rate per month slot, used at year boundary)
    lib = _make_library({
        "infl": _make_curve(name="infl", values=vals, start_year=2025, start_month=1),
    })
    inp = _make_inputs(periods=36, start_year=2025, start_month=1,
                       library=lib, inflation_power_curve="infl")
    out = calculate(inp)
    # Year 1 (periods 0-11): index = 1.0
    assert out.inflation_power_index[0] == pytest.approx(1.0)
    assert out.inflation_power_index[11] == pytest.approx(1.0)
    # Year 2 (periods 12-23): index = 1.02
    assert out.inflation_power_index[12] == pytest.approx(1.02)
    assert out.inflation_power_index[23] == pytest.approx(1.02)
    # Year 3 (periods 24-35): index = 1.02 * 1.02 = 1.0404
    assert out.inflation_power_index[24] == pytest.approx(1.0404)
    assert out.inflation_power_index[35] == pytest.approx(1.0404)


def test_missing_curve_reported():
    """Selecting a curve that doesn't exist adds it to missing_curves."""
    inp = _make_inputs(periods=12, capture_price_curve="nonexistent")
    out = calculate(inp)
    assert "nonexistent" in out.missing_curves
    assert out.capture_price_monthly == [0.0] * 12


def test_empty_selection_returns_zeros():
    """When selection is empty string, output is zeros regardless of library content."""
    lib = _make_library({
        "cap": _make_curve(name="cap", values=[100.0] * 12),
    })
    inp = _make_inputs(periods=12, library=lib, capture_price_curve="")
    out = calculate(inp)
    assert out.capture_price_monthly == [0.0] * 12
    assert out.missing_curves == []


def test_debt_vs_live_curves_different():
    """Debt-case and live curves can point to different underlying data."""
    lib = _make_library({
        "cap_live": _make_curve(name="cap_live", values=[100.0] * 12),
        "cap_debt": _make_curve(name="cap_debt", values=[80.0] * 12),
    })
    inp = _make_inputs(
        periods=12, library=lib,
        capture_price_curve="cap_live",
        capture_price_curve_debt="cap_debt",
    )
    out = calculate(inp)
    assert out.capture_price_monthly == [100.0] * 12
    assert out.capture_price_debt_monthly == [80.0] * 12


def test_multiple_categories():
    """Curves from different categories resolve independently."""
    lib = _make_library({
        "cap": _make_curve(name="cap", category="price", values=[100.0] * 12),
        "goo": _make_curve(name="goo", category="green", values=[20.0] * 12),
        "curt": _make_curve(name="curt", category="factor", values=[0.05] * 12),
    })
    inp = _make_inputs(
        periods=12, library=lib,
        capture_price_curve="cap",
        goo_curve="goo",
        curtailment_curve="curt",
    )
    out = calculate(inp)
    assert out.capture_price_monthly == [100.0] * 12
    assert out.goo_price_monthly == [20.0] * 12
    assert out.curtailment_monthly == [0.05] * 12


def test_interest_rate_curve():
    """Interest rate curve resolves correctly."""
    vals = [0.035 + 0.001 * i for i in range(12)]
    lib = _make_library({
        "ir": _make_curve(name="ir", category="rate", unit="%", values=vals),
    })
    inp = _make_inputs(periods=12, library=lib, interest_rate_curve="ir")
    out = calculate(inp)
    assert out.interest_rate_monthly == pytest.approx(vals)


def test_curtailment_curve():
    """Curtailment factors pass through correctly."""
    vals = [0.0, 0.0, 0.05, 0.05, 0.10, 0.10, 0.05, 0.05, 0.0, 0.0, 0.0, 0.0]
    lib = _make_library({
        "curt": _make_curve(name="curt", category="factor", unit="fraction", values=vals),
    })
    inp = _make_inputs(periods=12, library=lib, curtailment_curve="curt")
    out = calculate(inp)
    assert out.curtailment_monthly == vals


def test_goo_curve():
    """GoO price curve resolves correctly."""
    vals = [15.0] * 24
    lib = _make_library({
        "goo": _make_curve(name="goo", category="green", unit="DKK/MWh", values=vals),
    })
    inp = _make_inputs(periods=24, library=lib, goo_curve="goo")
    out = calculate(inp)
    assert out.goo_price_monthly == vals


def test_output_lengths_match_periods():
    """All time-series outputs have length == periods."""
    n = 36
    lib = _make_library({
        "c": _make_curve(name="c", values=[1.0] * 48),
    })
    inp = _make_inputs(
        periods=n, library=lib,
        capture_price_curve="c", bess_revenue_curve="c",
        wholesale_curve="c", interest_rate_curve="c",
        goo_curve="c", curtailment_curve="c",
        imbalance_curve="c", bess_opex_curve="c",
        inflation_power_curve="c", inflation_ppa_curve="c",
        inflation_goo_curve="c", inflation_om_curve="c",
        inflation_land_curve="c", inflation_opex_curve="c",
        capture_price_curve_debt="c", bess_revenue_curve_debt="c",
    )
    out = calculate(inp)
    ts_fields = [
        "capture_price_monthly", "bess_revenue_monthly",
        "wholesale_price_monthly", "interest_rate_monthly",
        "goo_price_monthly", "curtailment_monthly",
        "imbalance_cost_monthly", "bess_opex_monthly",
        "inflation_power_index", "inflation_ppa_index",
        "inflation_goo_index", "inflation_om_index",
        "inflation_land_index", "inflation_opex_index",
        "capture_price_debt_monthly", "bess_revenue_debt_monthly",
    ]
    for field in ts_fields:
        assert len(getattr(out, field)) == n, f"{field} length != {n}"


def test_selected_curves_dict_populated():
    """selected_curves dict contains only non-empty selections."""
    lib = _make_library({
        "cap": _make_curve(name="cap", values=[1.0] * 12),
        "ws": _make_curve(name="ws", values=[2.0] * 12),
    })
    inp = _make_inputs(
        periods=12, library=lib,
        capture_price_curve="cap",
        wholesale_curve="ws",
    )
    out = calculate(inp)
    assert "capture_price" in out.selected_curves
    assert out.selected_curves["capture_price"] == "cap"
    assert "wholesale" in out.selected_curves
    assert out.selected_curves["wholesale"] == "ws"
    # Fields left empty should not appear
    assert "bess_revenue" not in out.selected_curves
    assert "interest_rate" not in out.selected_curves
