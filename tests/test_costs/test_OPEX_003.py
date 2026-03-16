"""Tests for OPEX_003 — Wind OPEX 5-component module."""

import pytest
from modules.costs.OPEX_003 import (
    ComponentRate,
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _indexation_factor,
    _fixed_component,
    _year_groups,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cr(annual=1_000.0, inf_start=2025, start_factor=1.0) -> ComponentRate:
    return ComponentRate(
        annual_DKKk=annual,
        inflation_start_year=inf_start,
        indexation_start_factor=start_factor,
    )


def _zero_cr() -> ComponentRate:
    return _cr(0.0)


def _base_inputs(
    periods=24,
    start_year=2026,
    start_month=1,
    capacity_mw=50.0,
    om_annual=3_000.0,
) -> Inputs:
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        capacity_mw=capacity_mw,
        om=_cr(om_annual),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_capacity_must_be_positive():
    with pytest.raises(Exception):
        Inputs(
            periods=12, start_year=2026, start_month=1,
            capacity_mw=0.0,
            om=_cr(1000.0),
        )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs_instance():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_output_array_lengths():
    n = 36
    out = calculate(_base_inputs(periods=n))
    for field in ("om", "land_lease", "insurance", "grid_costs", "other", "total_opex"):
        arr = getattr(out, field)
        assert len(arr) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Single component: O&M
# ---------------------------------------------------------------------------

def test_om_monthly_is_annual_over_12():
    n = 12
    annual = 3_600.0
    inp = _base_inputs(n, om_annual=annual)
    inp = inp.model_copy(update={"inflation_rate": 0.0})
    out = calculate(inp)
    for p in range(n):
        assert out.om[p] == pytest.approx(annual / 12.0)


def test_zero_component_gives_zero_output():
    n = 12
    out = calculate(_base_inputs(n))
    for p in range(n):
        assert out.land_lease[p] == pytest.approx(0.0)
        assert out.insurance[p] == pytest.approx(0.0)
        assert out.grid_costs[p] == pytest.approx(0.0)
        assert out.other[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Total OPEX
# ---------------------------------------------------------------------------

def test_total_opex_is_sum_of_components():
    n = 12
    inp = Inputs(
        periods=n, start_year=2026, start_month=1,
        capacity_mw=50.0,
        om=_cr(1_200.0),
        land_lease=_cr(600.0),
        insurance=_cr(300.0),
        grid_costs=_cr(120.0),
        other=_cr(60.0),
    )
    out = calculate(inp)
    for p in range(n):
        expected = (out.om[p] + out.land_lease[p] + out.insurance[p]
                    + out.grid_costs[p] + out.other[p])
        assert out.total_opex[p] == pytest.approx(expected)


def test_lifetime_total_equals_sum():
    out = calculate(_base_inputs(periods=24))
    assert out.total_opex_lifetime == pytest.approx(sum(out.total_opex))


# ---------------------------------------------------------------------------
# Inflation
# ---------------------------------------------------------------------------

def test_indexation_factor_no_inflation():
    assert _indexation_factor(2025, 2025, 1.0, 0.0) == pytest.approx(1.0)


def test_indexation_factor_before_start_year():
    assert _indexation_factor(2024, 2025, 1.0, 0.05) == pytest.approx(1.0)


def test_indexation_factor_one_year():
    assert _indexation_factor(2026, 2025, 1.0, 0.025) == pytest.approx(1.025)


def test_component_increases_with_inflation():
    n = 24
    out = calculate(_base_inputs(n, start_year=2025, om_annual=1_200.0))
    # Period 12 (year 2026) should be higher than period 0 (year 2025)
    assert out.om[12] > out.om[0]


def test_component_with_start_factor():
    n = 12
    inp = Inputs(
        periods=n, start_year=2026, start_month=1,
        capacity_mw=50.0,
        om=_cr(1_200.0, inf_start=2026, start_factor=1.10),
    )
    out = calculate(inp)
    # First period: annual × 1.10 / 12
    assert out.om[0] == pytest.approx(1_200.0 * 1.10 / 12.0)


# ---------------------------------------------------------------------------
# Annual aggregation
# ---------------------------------------------------------------------------

def test_annual_opex_length():
    n = 24
    out = calculate(_base_inputs(periods=n, start_year=2026, start_month=1))
    assert len(out.annual_opex) == 2


def test_annual_opex_sum_equals_total_opex():
    out = calculate(_base_inputs(periods=24))
    assert sum(out.annual_opex) == pytest.approx(sum(out.total_opex))


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "start_factor": "B5", "inflation_rate": "B6", "year": "C3",
        "inf_start_year": "B7", "annual_base": "B10",
        "indexation_factor": "C8", "om": "C12", "other": "C16",
    }
    formulas = get_excel_formulas(refs)
    assert "indexation_factor" in formulas
    assert "fixed_component_monthly" in formulas
    assert "total_opex" in formulas
    for v in formulas.values():
        assert v.startswith("=")
