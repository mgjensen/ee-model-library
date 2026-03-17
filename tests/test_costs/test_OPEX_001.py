"""Tests for OPEX_001 — PV OPEX 11-component module."""

import pytest
from modules.costs.OPEX_001 import (
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
    capacity_mwp=100.0,
    om_annual=5_000.0,
    inflation_rate=0.025,
    commercial_management=None,
    inverter_replacement=None,
    insurance=None,
    other=None,
    **kwargs,
) -> Inputs:
    """Minimal Inputs with all optional components zeroed out by default."""
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        inflation_rate=inflation_rate,
        capacity_mwp=capacity_mwp,
        om=_cr(om_annual),
        commercial_management=commercial_management or _zero_cr(),
        inverter_replacement=inverter_replacement or _zero_cr(),
        insurance=insurance or _zero_cr(),
        other=other or _zero_cr(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Unit: _indexation_factor
# ---------------------------------------------------------------------------

def test_indexation_factor_no_inflation():
    assert _indexation_factor(2025, 2025, 1.0, 0.0) == pytest.approx(1.0)


def test_indexation_factor_before_start_year():
    """Year < inflation_start_year → factor = start_factor (no compounding)."""
    assert _indexation_factor(2024, 2025, 1.0, 0.05) == pytest.approx(1.0)


def test_indexation_factor_one_year():
    assert _indexation_factor(2026, 2025, 1.0, 0.025) == pytest.approx(1.025)


def test_indexation_factor_three_years():
    assert _indexation_factor(2028, 2025, 1.0, 0.025) == pytest.approx(1.025 ** 3)


def test_indexation_factor_pre_indexed():
    """start_factor=1.12 means the base is already 12% above the original rate."""
    f = _indexation_factor(2026, 2025, 1.12, 0.025)
    assert f == pytest.approx(1.12 * 1.025)


# ---------------------------------------------------------------------------
# Unit: _fixed_component
# ---------------------------------------------------------------------------

def test_fixed_component_zero_rate():
    yg = _year_groups(12, 2025, 1)
    result = _fixed_component(_cr(0.0), yg, 12, 0.025)
    assert all(v == 0.0 for v in result)


def test_fixed_component_monthly_accrual():
    """1,200 DKKk/year → 100 DKKk/month, no inflation."""
    yg = _year_groups(12, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.0), yg, 12, 0.0)
    assert all(v == pytest.approx(100.0) for v in result)


def test_fixed_component_inflation_increases_cost():
    """Cost in year 2 should be higher than year 1."""
    yg = _year_groups(24, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.0), yg, 24, 0.025)
    assert result[12] > result[0]  # year 2 > year 1


def test_fixed_component_year2_amount():
    """Verify year-2 amount = base × 1.025 / 12."""
    yg = _year_groups(24, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.0), yg, 24, 0.025)
    expected_year2 = 1_200.0 * 1.025 / 12.0
    assert result[12] == pytest.approx(expected_year2)


def test_fixed_component_pre_indexed():
    """start_factor=1.1 → year 1 cost = base × 1.1 / 12."""
    yg = _year_groups(12, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2025, 1.1), yg, 12, 0.025)
    expected = 1_200.0 * 1.1 / 12.0
    assert result[0] == pytest.approx(expected)


def test_fixed_component_inflation_starts_late():
    """inflation_start_year=2026 means 2025-2026 are at base, 2027 is first inflated year."""
    yg = _year_groups(36, 2025, 1)
    result = _fixed_component(_cr(1_200.0, 2026, 1.0), yg, 36, 0.025)
    assert result[0] == pytest.approx(result[12])    # 2025 == 2026 (no compounding yet)
    assert result[24] > result[12]                   # 2027 = base × 1.025^1


# ---------------------------------------------------------------------------
# Unit: calculate — component tests
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs_instance():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_calculate_arrays_correct_length():
    out = calculate(_base_inputs(periods=36))
    assert len(out.total_opex) == 36
    assert len(out.om) == 36


def test_calculate_om_only():
    """Single non-zero component: total = om."""
    out = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0))
    assert all(v == pytest.approx(1_000.0) for v in out.om)
    assert all(out.total_opex[p] == pytest.approx(out.om[p]) for p in range(24))


def test_calculate_inverter_zero_during_warranty():
    out = calculate(_base_inputs(
        periods=24,
        inverter_replacement=_cr(1_200.0),
        inverter_warranty_end_period=12,
    ))
    for p in range(12):
        assert out.inverter_replacement[p] == pytest.approx(0.0)
    for p in range(12, 24):
        assert out.inverter_replacement[p] > 0.0


def test_calculate_inverter_post_warranty():
    out = calculate(_base_inputs(
        periods=24,
        inverter_replacement=_cr(1_200.0, inf_start=2025),
        inverter_warranty_end_period=0,
        inflation_rate=0.0,
    ))
    # First 12 months in 2025: no compounding (inflation_start_year == start_year)
    assert all(v == pytest.approx(100.0) for v in out.inverter_replacement)


def test_calculate_land_lease_rented():
    out = calculate(_base_inputs(
        land_rented_ha=100.0,
        land_lease_rented=_cr(5_000.0, 2025),
        inflation_rate=0.0,
    ))
    expected = 5_000.0 / 12.0
    assert all(v == pytest.approx(expected) for v in out.land_lease_rented)


def test_calculate_land_lease_owned():
    out = calculate(_base_inputs(
        land_owned_ha=50.0,
        land_lease_owned=_cr(2_000.0, 2025),
        inflation_rate=0.0,
    ))
    expected = 2_000.0 / 12.0
    assert all(v == pytest.approx(expected) for v in out.land_lease_owned)


def test_calculate_lease_tax():
    out = calculate(_base_inputs(
        lease_tax=_cr(3_000.0, 2025),
        inflation_rate=0.0,
    ))
    expected = 3_000.0 / 12.0
    assert all(v == pytest.approx(expected) for v in out.lease_tax)


def test_calculate_insurance():
    out = calculate(_base_inputs(
        insurance=_cr(4_000.0, inf_start=2025),
        inflation_rate=0.0,
    ))
    assert all(v == pytest.approx(4_000.0 / 12.0) for v in out.insurance)


def test_calculate_guarantee():
    out = calculate(_base_inputs(
        guarantee_amount_DKKk=10_000.0,
        guarantee_rate=0.01,  # 1% annual
    ))
    expected = 10_000.0 * 0.01 / 12.0
    assert all(v == pytest.approx(expected) for v in out.guarantee)


def test_calculate_guarantee_zero_when_no_amount():
    out = calculate(_base_inputs())
    assert all(v == pytest.approx(0.0) for v in out.guarantee)


def test_calculate_ve_bonus():
    spot = [1_000.0] * 24  # DKKk/month spot revenue
    out = calculate(_base_inputs(
        ve_bonus_pct=0.05,
        spot_revenue=spot,
    ))
    assert all(v == pytest.approx(50.0) for v in out.ve_bonus)


def test_calculate_ve_bonus_proportional_to_spot():
    spot = [float(p * 100) for p in range(24)]
    out = calculate(_base_inputs(ve_bonus_pct=0.10, spot_revenue=spot))
    for p in range(24):
        assert out.ve_bonus[p] == pytest.approx(0.10 * spot[p])


def test_calculate_self_consumption():
    """100 MWp, 20 MWh/MWp/year, 500 DKK/MWh flat price → monthly cost."""
    price = [500.0] * 24
    out = calculate(_base_inputs(
        capacity_mwp=100.0,
        self_consumption_mwh_per_mwp_annual=20.0,
        wholesale_price=price,
        inflation_rate=0.0,
    ))
    # monthly energy = 100 × 20 / 12 = 166.67 MWh
    # monthly cost = 166.67 × 500 / 1000 = 83.33 DKKk
    expected = 100.0 * 20.0 / 12.0 * 500.0 / 1000.0
    assert all(v == pytest.approx(expected, rel=1e-5) for v in out.self_consumption)


def test_calculate_self_consumption_zero_when_not_set():
    out = calculate(_base_inputs())
    assert all(v == 0.0 for v in out.self_consumption)


def test_calculate_self_consumption_price_variation():
    """Higher price months cost more."""
    price = [300.0] * 6 + [600.0] * 6 + [300.0] * 12
    out = calculate(_base_inputs(
        capacity_mwp=100.0,
        self_consumption_mwh_per_mwp_annual=12.0,
        wholesale_price=price,
        inflation_rate=0.0,
    ))
    assert out.self_consumption[6] == pytest.approx(2 * out.self_consumption[0])


def test_calculate_other():
    out = calculate(_base_inputs(other=_cr(6_000.0, inf_start=2025), inflation_rate=0.0))
    assert all(v == pytest.approx(500.0) for v in out.other)


# ---------------------------------------------------------------------------
# Unit: total and annual aggregation
# ---------------------------------------------------------------------------

def test_calculate_total_is_sum_of_components():
    out = calculate(_base_inputs(
        om_annual=12_000.0,
        commercial_management=_cr(2_400.0),
        insurance=_cr(3_600.0, inf_start=2025),
        inflation_rate=0.0,
    ))
    for p in range(24):
        expected = (
            out.om[p] + out.commercial_management[p] + out.inverter_replacement[p]
            + out.land_lease_rented[p] + out.land_lease_owned[p] + out.lease_tax[p]
            + out.insurance[p] + out.guarantee[p] + out.ve_bonus[p]
            + out.self_consumption[p] + out.other[p]
        )
        assert out.total_opex[p] == pytest.approx(expected)


def test_calculate_annual_sums_monthly():
    out = calculate(_base_inputs(om_annual=12_000.0, inflation_rate=0.0))
    # 2 years of 24 months
    assert out.annual_opex[0] == pytest.approx(sum(out.total_opex[:12]))
    assert out.annual_opex[1] == pytest.approx(sum(out.total_opex[12:]))


def test_calculate_lifetime_total():
    out = calculate(_base_inputs(om_annual=12_000.0))
    assert out.total_opex_lifetime == pytest.approx(sum(out.total_opex))


def test_calculate_lifetime_positive():
    out = calculate(_base_inputs(om_annual=5_000.0))
    assert out.total_opex_lifetime > 0


# ---------------------------------------------------------------------------
# Integration: Viuf proxy (258 MWp, 25y, DK assumptions)
# ---------------------------------------------------------------------------

def test_viuf_proxy_opex_reasonable():
    """
    Proxy inputs for Project Viuf (258 MWp, ~40-year project).
    Validate: total lifetime OPEX is in a plausible range.
    ~1,222,811 DKKk per spec §16 (combined PV + BESS, so PV portion is a subset).
    """
    periods = 475
    out = calculate(Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        inflation_rate=0.025,
        capacity_mwp=258.0,
        om=ComponentRate(annual_DKKk=15_000.0, inflation_start_year=2026),
        commercial_management=ComponentRate(annual_DKKk=1_500.0, inflation_start_year=2026),
        inverter_replacement=ComponentRate(annual_DKKk=2_000.0, inflation_start_year=2026),
        inverter_warranty_end_period=120,
        land_rented_ha=500.0,
        land_lease_rented=ComponentRate(annual_DKKk=3_000.0, inflation_start_year=2026),
        insurance=ComponentRate(annual_DKKk=5_000.0, inflation_start_year=2026),
        other=ComponentRate(annual_DKKk=1_000.0, inflation_start_year=2026),
    ))
    assert out.total_opex_lifetime > 200_000.0   # DKKk — plausible floor
    assert out.total_opex_lifetime < 3_000_000.0  # DKKk — plausible ceiling
    assert len(out.total_opex) == 475


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_ve_bonus_requires_spot_revenue():
    with pytest.raises(ValueError, match="spot_revenue"):
        _base_inputs(ve_bonus_pct=0.05, spot_revenue=[])  # missing


def test_self_consumption_requires_wholesale_price():
    with pytest.raises(ValueError, match="wholesale_price"):
        _base_inputs(
            self_consumption_mwh_per_mwp_annual=10.0,
            wholesale_price=[],  # missing
        )


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "start_factor": "B10", "inflation_rate": "B11", "year": "K3",
        "inf_start_year": "B12", "annual_base": "G20", "indexation_factor": "K$5",
        "inv_base": "G21", "warranty_end": "B13", "period": "K$4",
        "capacity_mwp": "B5", "sc_mwh_per_mwp": "B6", "wholesale_price": "K50",
        "ve_pct": "B7", "spot_revenue": "K45", "guarantee_amount": "G8",
        "guarantee_rate": "B8", "om": "K20", "other": "K30",
    }
    formulas = get_excel_formulas(refs)
    for key in ("indexation_factor", "fixed_component_monthly", "inverter_replacement",
                "self_consumption", "ve_bonus", "guarantee_interest", "total_opex"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# GAP 3: Multi-period O&M contracts
# ---------------------------------------------------------------------------

def test_om_config_multi_period_stepped_cost():
    """7-period O&M with varying costs produces correct stepped output."""
    from modules.costs.OPEX_001 import ContractPeriod, OMConfig
    om_cfg = OMConfig(
        periods=[
            ContractPeriod(cost_DKKk_per_unit_annual=100.0, duration_years=5),
            ContractPeriod(cost_DKKk_per_unit_annual=150.0, duration_years=5),
        ],
        unit_count=10.0,
    )
    out = calculate(_base_inputs(
        periods=120,
        start_year=2025,
        start_month=1,
        om_annual=0.0,
        inflation_rate=0.0,
        om_config=om_cfg,
    ))
    # First 5 years (periods 0-59): 100 * 10 / 12 = 83.333
    assert out.om[0] == pytest.approx(100.0 * 10.0 / 12.0, rel=1e-4)
    assert out.om[59] == pytest.approx(100.0 * 10.0 / 12.0, rel=1e-4)
    # Years 6-10 (periods 60-119): 150 * 10 / 12 = 125.0
    assert out.om[60] == pytest.approx(150.0 * 10.0 / 12.0, rel=1e-4)
    assert out.om[119] == pytest.approx(150.0 * 10.0 / 12.0, rel=1e-4)
    # Step change: year 6 cost > year 5 cost
    assert out.om[60] > out.om[59]


def test_om_config_minimum_floor_binding():
    """Minimum cost floor is binding when rate < floor."""
    from modules.costs.OPEX_001 import ContractPeriod, OMConfig
    om_cfg = OMConfig(
        periods=[
            ContractPeriod(
                cost_DKKk_per_unit_annual=1.0,
                duration_years=10,
                minimum_cost_DKKk_annual=500.0,
            ),
        ],
        unit_count=1.0,
    )
    out = calculate(_base_inputs(
        periods=120,
        start_year=2025,
        start_month=1,
        om_annual=0.0,
        inflation_rate=0.0,
        om_config=om_cfg,
    ))
    # rate_annual = 1.0 * 1.0 = 1.0, min = 500.0 → effective = 500/12
    for p in range(12):
        assert out.om[p] >= 500.0 / 12.0 - 0.01


def test_om_config_empty_falls_back():
    """Empty OMConfig periods list falls back to ComponentRate."""
    from modules.costs.OPEX_001 import OMConfig
    om_cfg = OMConfig(periods=[], unit_count=10.0)
    out = calculate(_base_inputs(
        periods=24,
        om_annual=12_000.0,
        inflation_rate=0.0,
        om_config=om_cfg,
    ))
    # Should use the ComponentRate om=12000 → 1000/month
    assert all(v == pytest.approx(1_000.0) for v in out.om)
