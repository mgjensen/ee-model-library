"""Tests for OPEX_002 — BESS OPEX 4-component module."""

import pytest
from modules.costs.OPEX_002 import (
    ComponentRate,
    NamedComponent,
    VariableComponent,
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

def _cr(annual=1_000.0, inf_start=2025, start_factor=1.0):
    return ComponentRate(
        annual_DKKk=annual,
        inflation_start_year=inf_start,
        indexation_start_factor=start_factor,
    )


def _zero_cr():
    return _cr(0.0)


def _base_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    inflation_rate=0.025,
    om_annual=5_000.0,
    insurance=None,
    other=None,
    **kwargs,
) -> Inputs:
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        inflation_rate=inflation_rate,
        om=_cr(om_annual),
        insurance=insurance or _zero_cr(),
        other=other or _zero_cr(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Unit: _indexation_factor
# ---------------------------------------------------------------------------

def test_indexation_factor_no_inflation():
    assert _indexation_factor(2025, 2025, 1.0, 0.0) == pytest.approx(1.0)


def test_indexation_factor_before_start():
    assert _indexation_factor(2024, 2025, 1.0, 0.05) == pytest.approx(1.0)


def test_indexation_factor_year1():
    assert _indexation_factor(2026, 2025, 1.0, 0.025) == pytest.approx(1.025)


def test_indexation_factor_three_years():
    assert _indexation_factor(2028, 2025, 1.0, 0.025) == pytest.approx(1.025 ** 3)


def test_indexation_factor_pre_indexed():
    assert _indexation_factor(2026, 2025, 1.2, 0.025) == pytest.approx(1.2 * 1.025)


# ---------------------------------------------------------------------------
# Unit: _fixed_component
# ---------------------------------------------------------------------------

def test_fixed_component_zero():
    yg = _year_groups(12, 2025, 1)
    assert all(v == 0.0 for v in _fixed_component(_cr(0.0), yg, 12, 0.025))


def test_fixed_component_monthly_accrual():
    yg = _year_groups(12, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.0), yg, 12, 0.0)
    assert all(v == pytest.approx(100.0) for v in result)


def test_fixed_component_inflation_increases_year2():
    yg = _year_groups(24, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.0), yg, 24, 0.025)
    assert result[12] == pytest.approx(1_200.0 * 1.025 / 12.0)


def test_fixed_component_pre_indexed():
    yg = _year_groups(12, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.1), yg, 12, 0.025)
    assert result[0] == pytest.approx(1_200.0 * 1.1 / 12.0)


# ---------------------------------------------------------------------------
# Unit: calculate — component tests
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_calculate_array_length():
    out = calculate(_base_inputs(periods=36))
    assert len(out.total_opex) == 36
    assert len(out.om) == 36
    assert len(out.insurance) == 36
    assert len(out.trading_costs) == 36
    assert len(out.other) == 36


def test_calculate_om_only():
    out = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0))
    assert all(v == pytest.approx(1_000.0) for v in out.om)
    assert all(out.total_opex[p] == pytest.approx(out.om[p]) for p in range(24))


def test_calculate_om_inflation():
    out = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.025))
    assert out.om[12] == pytest.approx(12_000.0 * 1.025 / 12.0)


def test_calculate_insurance():
    out = calculate(_base_inputs(insurance=_cr(3_600.0, 2025), inflation_rate=0.0))
    assert all(v == pytest.approx(300.0) for v in out.insurance)


def test_calculate_insurance_inflation():
    out = calculate(_base_inputs(insurance=_cr(3_600.0, 2025), inflation_rate=0.025))
    assert out.insurance[12] == pytest.approx(3_600.0 * 1.025 / 12.0)


def test_calculate_other():
    out = calculate(_base_inputs(other=_cr(2_400.0, 2025), inflation_rate=0.0))
    assert all(v == pytest.approx(200.0) for v in out.other)


# ---------------------------------------------------------------------------
# Unit: calculate — trading costs (volume-dependent)
# ---------------------------------------------------------------------------

def test_calculate_trading_zero_when_rate_zero():
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=0.0,
        discharge_volume_MWh=[100.0] * 24,
    ))
    assert all(v == pytest.approx(0.0) for v in out.trading_costs)


def test_calculate_trading_basic():
    """10 DKK/MWh × 500 MWh / 1000 = 5 DKKk/month."""
    vol = [500.0] * 24
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=10.0,
        discharge_volume_MWh=vol,
        inflation_rate=0.0,
    ))
    assert all(v == pytest.approx(5.0) for v in out.trading_costs)


def test_calculate_trading_proportional_to_volume():
    vol = [float(p * 100) for p in range(24)]
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=5.0,
        discharge_volume_MWh=vol,
        inflation_rate=0.0,
    ))
    for p in range(24):
        assert out.trading_costs[p] == pytest.approx(vol[p] * 5.0 / 1000.0)


def test_calculate_trading_inflation_applied():
    """Trading rate in year 2 should be higher than year 1."""
    vol = [1_000.0] * 24
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=10.0,
        discharge_volume_MWh=vol,
        trading_inflation_start_year=2025,
        inflation_rate=0.025,
    ))
    assert out.trading_costs[12] > out.trading_costs[0]


def test_calculate_trading_inflation_year2_value():
    vol = [1_000.0] * 24
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=10.0,
        discharge_volume_MWh=vol,
        trading_inflation_start_year=2025,
        inflation_rate=0.025,
    ))
    expected_year2 = 1_000.0 * 10.0 * 1.025 / 1000.0
    assert out.trading_costs[12] == pytest.approx(expected_year2)


def test_calculate_trading_pre_indexed():
    vol = [1_000.0] * 12
    out = calculate(_base_inputs(
        periods=12,
        trading_cost_DKK_per_MWh=10.0,
        discharge_volume_MWh=vol,
        trading_inflation_start_year=2025,
        trading_indexation_start_factor=1.15,
        inflation_rate=0.0,
    ))
    expected = 1_000.0 * 10.0 * 1.15 / 1000.0
    assert all(v == pytest.approx(expected) for v in out.trading_costs)


def test_calculate_trading_zero_volume():
    vol = [0.0] * 24
    out = calculate(_base_inputs(
        trading_cost_DKK_per_MWh=10.0,
        discharge_volume_MWh=vol,
    ))
    assert all(v == pytest.approx(0.0) for v in out.trading_costs)


# ---------------------------------------------------------------------------
# Unit: total and annual aggregation
# ---------------------------------------------------------------------------

def test_calculate_total_is_sum_of_components():
    vol = [500.0] * 24
    out = calculate(_base_inputs(
        om_annual=12_000.0,
        insurance=_cr(3_600.0),
        other=_cr(2_400.0),
        trading_cost_DKK_per_MWh=8.0,
        discharge_volume_MWh=vol,
        inflation_rate=0.0,
    ))
    for p in range(24):
        expected = out.om[p] + out.insurance[p] + out.trading_costs[p] + out.other[p]
        assert out.total_opex[p] == pytest.approx(expected)


def test_calculate_annual_sums_monthly():
    out = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0))
    assert out.annual_opex[0] == pytest.approx(sum(out.total_opex[:12]))
    assert out.annual_opex[1] == pytest.approx(sum(out.total_opex[12:]))


def test_calculate_annual_count():
    out = calculate(_base_inputs(periods=36, start_year=2025, start_month=1))
    assert len(out.annual_opex) == 3  # 2025, 2026, 2027


def test_calculate_lifetime_total():
    out = calculate(_base_inputs(om_annual=12_000.0))
    assert out.total_opex_lifetime == pytest.approx(sum(out.total_opex))


def test_calculate_lifetime_positive():
    out = calculate(_base_inputs(om_annual=5_000.0))
    assert out.total_opex_lifetime > 0


def test_calculate_all_zero_gives_zero_lifetime():
    out = calculate(_base_inputs(om_annual=0.0))
    assert out.total_opex_lifetime == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Integration: Viuf BESS proxy (25 MW / 100 MWh, 475 periods)
# ---------------------------------------------------------------------------

def test_viuf_bess_proxy():
    """
    Proxy inputs for Viuf BESS (25 MW / 100 MWh).
    Validate lifetime OPEX is in a plausible DKKk range.
    """
    periods = 475
    avg_daily_cycles = 1.5
    capacity_mwh = 100.0
    avg_monthly_discharge = capacity_mwh * avg_daily_cycles * 30.5
    discharge_vol = [avg_monthly_discharge] * periods

    out = calculate(Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        inflation_rate=0.025,
        om=ComponentRate(annual_DKKk=3_000.0, inflation_start_year=2026),
        insurance=ComponentRate(annual_DKKk=1_500.0, inflation_start_year=2026),
        trading_cost_DKK_per_MWh=2.0,
        trading_inflation_start_year=2026,
        discharge_volume_MWh=discharge_vol,
        other=ComponentRate(annual_DKKk=500.0, inflation_start_year=2026),
    ))

    assert out.total_opex_lifetime > 50_000.0    # DKKk — plausible floor
    assert out.total_opex_lifetime < 2_000_000.0  # DKKk — plausible ceiling
    assert len(out.total_opex) == 475
    assert len(out.annual_opex) > 0


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_trading_requires_volume_series():
    with pytest.raises(ValueError, match="discharge_volume_MWh"):
        _base_inputs(trading_cost_DKK_per_MWh=5.0, discharge_volume_MWh=[])


def test_trading_volume_wrong_length():
    with pytest.raises(ValueError, match="discharge_volume_MWh"):
        _base_inputs(
            periods=24,
            trading_cost_DKK_per_MWh=5.0,
            discharge_volume_MWh=[100.0] * 12,  # too short
        )


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "start_factor": "B10", "inflation_rate": "B11", "year": "K3",
        "inf_start_year": "B12", "annual_base": "G20", "indexation_factor": "K$5",
        "trading_rate": "B15", "discharge_vol": "K40", "om": "K20", "other": "K25",
    }
    formulas = get_excel_formulas(refs)
    for key in ("indexation_factor", "fixed_component_monthly", "trading_costs", "total_opex"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# Extra + variable component tests
# ---------------------------------------------------------------------------

def _nc(name="ltsa", annual=2_400.0, inf_start=2025, start_factor=1.0):
    return NamedComponent(
        name=name,
        rate=ComponentRate(annual_DKKk=annual, inflation_start_year=inf_start,
                           indexation_start_factor=start_factor),
    )


def _vc(name="fcas_costs", cost_per_unit=5.0, volume=None, n=24,
        inf_start=2025, start_factor=1.0):
    return VariableComponent(
        name=name, cost_per_unit=cost_per_unit,
        volume=volume or [100.0] * n,
        inflation_start_year=inf_start,
        indexation_start_factor=start_factor,
    )


def test_extra_empty():
    out_base = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0))
    out_ext = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0,
                                     extra_components=[]))
    assert out_ext.extra_costs == {}
    assert out_ext.variable_costs == {}
    assert out_ext.total_opex == out_base.total_opex


def test_extra_single():
    out = calculate(_base_inputs(om_annual=0.0, inflation_rate=0.0,
                                 extra_components=[_nc("ltsa", annual=1_200.0)]))
    assert "ltsa" in out.extra_costs
    assert out.extra_costs["ltsa"][0] == pytest.approx(100.0)  # 1200/12
    assert out.total_opex[0] == pytest.approx(100.0)


def test_extra_multiple():
    extras = [
        _nc("ltsa", annual=1_200.0),
        _nc("fire_levy", annual=600.0),
        _nc("pilor", annual=2_400.0),
        _nc("land_lease", annual=3_600.0),
        _nc("network", annual=1_800.0),
    ]
    out = calculate(_base_inputs(om_annual=0.0, inflation_rate=0.0,
                                 extra_components=extras))
    assert len(out.extra_costs) == 5
    expected_total = sum(ec.rate.annual_DKKk / 12.0 for ec in extras)
    assert out.total_opex[0] == pytest.approx(expected_total)


def test_variable_single():
    vol = [200.0] * 24
    out = calculate(_base_inputs(om_annual=0.0, inflation_rate=0.0,
                                 variable_components=[_vc("fcas", 10.0, vol)]))
    # 200 * 10 / 1000 = 2.0 DKKk
    assert "fcas" in out.variable_costs
    assert out.variable_costs["fcas"][0] == pytest.approx(2.0)
    assert out.total_opex[0] == pytest.approx(2.0)


def test_variable_plus_extras():
    extras = [_nc("ltsa", annual=1_200.0)]
    variables = [_vc("fcas", 10.0, [200.0] * 24)]
    out = calculate(_base_inputs(om_annual=0.0, inflation_rate=0.0,
                                 extra_components=extras,
                                 variable_components=variables))
    expected = 100.0 + 2.0  # ltsa + fcas
    assert out.total_opex[0] == pytest.approx(expected)


def test_variable_bad_length():
    with pytest.raises(ValueError, match="variable_component.*volume length"):
        _base_inputs(variable_components=[_vc("bad", 5.0, [100.0] * 12, n=12)])


def test_sensitivity_applies_to_extras():
    extras = [_nc("ltsa", annual=1_200.0)]
    variables = [_vc("fcas", 10.0, [200.0] * 24)]
    out = calculate(_base_inputs(om_annual=0.0, inflation_rate=0.0,
                                 extra_components=extras,
                                 variable_components=variables,
                                 sensitivity_factor=1.5))
    expected = (100.0 + 2.0) * 1.5
    assert out.total_opex[0] == pytest.approx(expected)
