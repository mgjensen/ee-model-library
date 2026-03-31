"""Tests for REV_002 — BESS revenue module, sub-sections A–G."""

import pytest
from modules.revenue.REV_002 import (
    Inputs,
    Outputs,
    VariableTariff,
    SystemTariff,
    calculate,
    get_excel_formulas,
    _year_groups,
    _indexation_factor,
    _factor_series,
    _variable_tariff_cost,
    _system_tariff_series,
    _combined_export_tariff_rate_inflated,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat(
    periods=24,
    start_year=2025,
    start_month=1,
    power_MW=100.0,
    energy_MWh=400.0,
    rte=0.85,
    dis_vol=500.0,           # MWh/month
    dis_price=800.0,          # DKK/MWh
    grid_chg_vol=300.0,       # MWh/month
    grid_chg_price=400.0,     # DKK/MWh
    pv_chg_vol=200.0,         # MWh/month
    pv_chg_price=350.0,       # DKK/MWh
    goo_price=10.0,           # DKK/MWh
    multi_pct=0.0,
    inflation_rate=0.0,
    **kwargs,
) -> Inputs:
    n = periods
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        inflation_rate=inflation_rate,
        power_MW=power_MW,
        energy_MWh_installed=energy_MWh,
        round_trip_efficiency=rte,
        discharge_volume_MWh=[dis_vol] * n,
        discharge_price_DKK_per_MWh=[dis_price] * n,
        grid_charge_volume_MWh=[grid_chg_vol] * n,
        grid_charge_price_DKK_per_MWh=[grid_chg_price] * n,
        pv_charge_volume_MWh=[pv_chg_vol] * n,
        pv_charge_price_DKK_per_MWh=[pv_chg_price] * n,
        goo_price_DKK_per_MWh=[goo_price] * n,
        multimarket_pct=[multi_pct] * n,
        **kwargs,
    )


def _vt(rate=1.0, inf_start=2025, start_factor=1.0) -> VariableTariff:
    return VariableTariff(
        rate_DKK_per_MWh=rate,
        inflation_start_year=inf_start,
        indexation_start_factor=start_factor,
    )


# ---------------------------------------------------------------------------
# Unit: helpers
# ---------------------------------------------------------------------------

def test_indexation_factor_no_inflation():
    assert _indexation_factor(2025, 2025, 1.0, 0.0) == pytest.approx(1.0)


def test_indexation_factor_before_start():
    assert _indexation_factor(2024, 2025, 1.0, 0.10) == pytest.approx(1.0)


def test_indexation_factor_year2():
    assert _indexation_factor(2026, 2025, 1.0, 0.05) == pytest.approx(1.05)


def test_factor_series_length():
    yg = _year_groups(24, 2025, 1)
    result = _factor_series(yg, 24, 2025, 1.0, 0.05)
    assert len(result) == 24


def test_factor_series_year2_higher():
    yg = _year_groups(24, 2025, 1)
    result = _factor_series(yg, 24, 2025, 1.0, 0.05)
    assert result[12] > result[0]


def test_variable_tariff_cost_basic():
    yg = _year_groups(12, 2025, 1)
    volume = [1_000.0] * 12
    cost = _variable_tariff_cost(_vt(rate=2.0), volume, yg, 12, 0.0)
    assert all(v == pytest.approx(2.0) for v in cost)  # 1000 × 2 / 1000 = 2 DKKk


def test_variable_tariff_cost_zero_rate():
    yg = _year_groups(12, 2025, 1)
    cost = _variable_tariff_cost(_vt(0.0), [1000.0] * 12, yg, 12, 0.0)
    assert all(v == 0.0 for v in cost)


def test_system_tariff_all_tier1():
    """All volume within tier1 threshold → only tier1 rate applies."""
    yg = _year_groups(12, 2025, 1)
    st = SystemTariff(
        tier1_threshold_MWh_annual=100_000.0,
        tier1_rate_DKK_per_MWh=5.0,
        tier2_rate_DKK_per_MWh=2.0,
    )
    vol = [500.0] * 12  # annual = 6,000 < 100,000
    cost = _system_tariff_series(vol, st, yg, 12, 0.0)
    expected = 500.0 * 5.0 / 1000.0  # = 2.5 DKKk
    assert all(v == pytest.approx(expected) for v in cost)


def test_system_tariff_crosses_threshold():
    """Annual volume crosses threshold partway through the year."""
    yg = _year_groups(12, 2025, 1)
    st = SystemTariff(
        tier1_threshold_MWh_annual=3_000.0,  # crossed after month 6 (500×6=3000)
        tier1_rate_DKK_per_MWh=5.0,
        tier2_rate_DKK_per_MWh=2.0,
    )
    vol = [500.0] * 12
    cost = _system_tariff_series(vol, st, yg, 12, 0.0)
    # Months 0-5: tier 1 (500 × 5 / 1000 = 2.5 DKKk each)
    for p in range(6):
        assert cost[p] == pytest.approx(2.5)
    # Month 6+: tier 2 (500 × 2 / 1000 = 1.0 DKKk each)
    for p in range(6, 12):
        assert cost[p] == pytest.approx(1.0)


def test_system_tariff_resets_each_year():
    """Annual threshold resets at start of each calendar year."""
    yg = _year_groups(24, 2025, 1)
    st = SystemTariff(
        tier1_threshold_MWh_annual=3_000.0,
        tier1_rate_DKK_per_MWh=5.0,
        tier2_rate_DKK_per_MWh=2.0,
    )
    vol = [500.0] * 24
    cost = _system_tariff_series(vol, st, yg, 24, 0.0)
    # Year 1 month 0 and year 2 month 0 should both start at tier1 rate
    assert cost[0] == pytest.approx(cost[12])  # both at tier1


def test_system_tariff_inflation():
    yg = _year_groups(24, 2025, 1)
    st = SystemTariff(
        tier1_threshold_MWh_annual=100_000.0,
        tier1_rate_DKK_per_MWh=5.0,
        tier2_rate_DKK_per_MWh=2.0,
        inflation_start_year=2025,
    )
    cost = _system_tariff_series([500.0] * 24, st, yg, 24, 0.10)
    assert cost[12] == pytest.approx(cost[0] * 1.10, rel=1e-6)


def test_combined_export_tariff_rate():
    yg = _year_groups(12, 2025, 1)
    tso = _vt(2.0)
    dso = _vt(1.0)
    np  = _vt(0.5)
    combined = _combined_export_tariff_rate_inflated(tso, dso, np, yg, 12, 0.0)
    assert all(v == pytest.approx(3.5) for v in combined)


# ---------------------------------------------------------------------------
# Section A — Discharge revenue
# ---------------------------------------------------------------------------

def test_a_discharge_volume_passthrough():
    out = calculate(_flat(dis_vol=1_234.5))
    assert all(v == pytest.approx(1_234.5) for v in out.discharge_volume)


def test_a_no_inflation_factor_is_one():
    out = calculate(_flat(inflation_rate=0.0))
    assert all(f == pytest.approx(1.0) for f in out.discharge_indexation_factor)


def test_a_inflation_applied_to_price():
    out = calculate(_flat(dis_price=800.0, inflation_rate=0.10))
    assert out.discharge_price_inflated[12] == pytest.approx(800.0 * 1.10)


def test_a_discharge_revenue_formula():
    """discharge_revenue = volume × inflated_price / 1000"""
    out = calculate(_flat(dis_vol=500.0, dis_price=800.0, inflation_rate=0.0))
    expected = 500.0 * 800.0 / 1000.0  # = 400 DKKk
    assert all(v == pytest.approx(expected) for v in out.discharge_revenue)


def test_a_discharge_revenue_inflated_year2():
    out = calculate(_flat(dis_vol=500.0, dis_price=800.0, inflation_rate=0.05))
    assert out.discharge_revenue[12] == pytest.approx(500.0 * 800.0 * 1.05 / 1000.0)


# ---------------------------------------------------------------------------
# Section B — Charging costs
# ---------------------------------------------------------------------------

def test_b_grid_charging_cost():
    out = calculate(_flat(grid_chg_vol=300.0, grid_chg_price=400.0, inflation_rate=0.0))
    expected = 300.0 * 400.0 / 1000.0  # = 120 DKKk
    assert all(v == pytest.approx(expected) for v in out.grid_charging_cost)


def test_b_pv_charging_cost():
    out = calculate(_flat(pv_chg_vol=200.0, pv_chg_price=350.0, inflation_rate=0.0))
    expected = 200.0 * 350.0 / 1000.0  # = 70 DKKk
    assert all(v == pytest.approx(expected) for v in out.pv_charging_cost)


def test_b_total_charging_cost_sums():
    out = calculate(_flat())
    for p in range(24):
        assert out.total_charging_cost[p] == pytest.approx(
            out.grid_charging_cost[p] + out.pv_charging_cost[p]
        )


def test_b_charge_inflation_applied():
    out = calculate(_flat(grid_chg_price=400.0, inflation_rate=0.05))
    assert out.grid_charge_price_inflated[12] == pytest.approx(400.0 * 1.05)


def test_b_charge_inflation_increases_cost():
    out = calculate(_flat(grid_chg_vol=300.0, grid_chg_price=400.0, inflation_rate=0.05))
    assert out.grid_charging_cost[12] > out.grid_charging_cost[0]


# ---------------------------------------------------------------------------
# Section C — Import production costs
# ---------------------------------------------------------------------------

def test_c_feed_in_tariff_cost():
    out = calculate(_flat(
        grid_chg_vol=1_000.0, inflation_rate=0.0,
        feed_in_tariff=_vt(3.0),
    ))
    expected = 1_000.0 * 3.0 / 1000.0  # = 3 DKKk
    assert all(v == pytest.approx(expected) for v in out.feed_in_tariff_cost)


def test_c_net_loss_tariff():
    """net_loss_cost = pct × (grid_price_inflated + markup) × volume / 1000"""
    out = calculate(_flat(
        grid_chg_vol=1_000.0, grid_chg_price=400.0, inflation_rate=0.0,
        net_loss_pct=0.05, net_loss_markup_DKK_per_MWh=10.0,
    ))
    expected = 0.05 * (400.0 + 10.0) * 1_000.0 / 1000.0  # = 20.5 DKKk
    assert all(v == pytest.approx(expected) for v in out.net_loss_tariff_cost)


def test_c_net_loss_zero_when_pct_zero():
    out = calculate(_flat(net_loss_pct=0.0))
    assert all(v == pytest.approx(0.0) for v in out.net_loss_tariff_cost)


def test_c_system_tariff_present():
    st = SystemTariff(
        tier1_threshold_MWh_annual=2_000.0,
        tier1_rate_DKK_per_MWh=5.0,
        tier2_rate_DKK_per_MWh=2.0,
    )
    out = calculate(_flat(grid_chg_vol=500.0, inflation_rate=0.0, system_tariff=st))
    # 500 × 4 months = 2000 hits threshold; months 1-4 tier1, 5+ tier2
    assert out.system_tariff_cost[0] == pytest.approx(500.0 * 5.0 / 1000.0)
    assert out.system_tariff_cost[4] == pytest.approx(500.0 * 2.0 / 1000.0)


def test_c_grid_import_fee_flat_monthly():
    """100 MW × 1,200 DKK/MW/year → 120,000 DKK/year → 10,000 DKK/month = 10 DKKk"""
    out = calculate(_flat(
        power_MW=100.0, inflation_rate=0.0,
        grid_import_fee_DKK_per_MW_annual=1_200.0,
    ))
    expected = 100.0 * 1_200.0 / 12.0 / 1000.0  # = 10 DKKk
    assert all(v == pytest.approx(expected) for v in out.grid_import_fee)


def test_c_total_import_costs_sums_components():
    st = SystemTariff(tier1_threshold_MWh_annual=100_000.0, tier1_rate_DKK_per_MWh=3.0)
    out = calculate(_flat(
        inflation_rate=0.0,
        feed_in_tariff=_vt(2.0),
        net_loss_pct=0.02,
        system_tariff=st,
        grid_import_fee_DKK_per_MW_annual=500.0,
    ))
    for p in range(24):
        expected = (
            out.feed_in_tariff_cost[p]
            + out.net_loss_tariff_cost[p]
            + out.system_tariff_cost[p]
            + out.grid_import_fee[p]
        )
        assert out.total_import_production_costs[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Section D — Export production costs
# ---------------------------------------------------------------------------

def test_d_tso_export_cost():
    out = calculate(_flat(dis_vol=500.0, inflation_rate=0.0, tso_export_tariff=_vt(2.0)))
    expected = 500.0 * 2.0 / 1000.0  # = 1 DKKk
    assert all(v == pytest.approx(expected) for v in out.tso_export_cost)


def test_d_dso_export_cost():
    out = calculate(_flat(dis_vol=500.0, inflation_rate=0.0, dso_export_tariff=_vt(1.5)))
    assert all(v == pytest.approx(0.75) for v in out.dso_export_cost)


def test_d_nordpool_export_cost():
    out = calculate(_flat(dis_vol=500.0, inflation_rate=0.0, nordpool_export_tariff=_vt(0.5)))
    assert all(v == pytest.approx(0.25) for v in out.nordpool_export_cost)


def test_d_export_inflation():
    out = calculate(_flat(dis_vol=500.0, inflation_rate=0.10, tso_export_tariff=_vt(2.0)))
    assert out.tso_export_cost[12] == pytest.approx(500.0 * 2.0 * 1.10 / 1000.0)


def test_d_total_export_costs_sums():
    out = calculate(_flat(
        inflation_rate=0.0,
        tso_export_tariff=_vt(2.0),
        dso_export_tariff=_vt(1.0),
        nordpool_export_tariff=_vt(0.5),
    ))
    for p in range(24):
        assert out.total_export_production_costs[p] == pytest.approx(
            out.tso_export_cost[p] + out.dso_export_cost[p] + out.nordpool_export_cost[p]
        )


# ---------------------------------------------------------------------------
# Section E — System adjustments
# ---------------------------------------------------------------------------

def test_e_goo_lost_volume():
    """goo_lost = pv_charge_vol × (1 - RTE)"""
    out = calculate(_flat(pv_chg_vol=200.0, rte=0.85))
    expected = 200.0 * (1.0 - 0.85)  # = 30 MWh
    assert all(v == pytest.approx(expected) for v in out.goo_lost_volume)


def test_e_goo_lost_volume_zero_pv():
    out = calculate(_flat(pv_chg_vol=0.0))
    assert all(v == pytest.approx(0.0) for v in out.goo_lost_volume)


def test_e_goo_revenue_lost_negative():
    """GoO revenue lost is a cost → negative values."""
    out = calculate(_flat(pv_chg_vol=200.0, rte=0.85, goo_price=10.0))
    assert all(v <= 0.0 for v in out.goo_revenue_lost)


def test_e_goo_revenue_lost_value():
    """goo_revenue_lost = −lost_vol × goo_price / 1000"""
    out = calculate(_flat(pv_chg_vol=200.0, rte=0.85, goo_price=10.0, inflation_rate=0.0))
    lost_vol = 200.0 * 0.15  # 30 MWh
    expected = -lost_vol * 10.0 / 1000.0  # = -0.3 DKKk
    assert all(v == pytest.approx(expected) for v in out.goo_revenue_lost)


def test_e_export_tariff_addback_positive():
    out = calculate(_flat(
        pv_chg_vol=200.0, rte=0.85,
        tso_export_tariff=_vt(2.0), inflation_rate=0.0,
    ))
    assert all(v >= 0.0 for v in out.export_tariff_addback)


def test_e_export_tariff_addback_value():
    """addback = lost_vol × combined_rate / 1000"""
    out = calculate(_flat(
        pv_chg_vol=200.0, rte=0.85,
        tso_export_tariff=_vt(2.0),
        dso_export_tariff=_vt(1.0),
        nordpool_export_tariff=_vt(0.5),
        inflation_rate=0.0,
    ))
    lost_vol = 200.0 * 0.15
    expected = lost_vol * 3.5 / 1000.0
    assert all(v == pytest.approx(expected) for v in out.export_tariff_addback)


def test_e_total_adjustments_equals_addback_plus_goo_lost():
    out = calculate(_flat(pv_chg_vol=200.0, rte=0.85, goo_price=10.0, tso_export_tariff=_vt(2.0)))
    for p in range(24):
        assert out.total_system_adjustments[p] == pytest.approx(
            out.export_tariff_addback[p] + out.goo_revenue_lost[p]
        )


def test_e_zero_pv_charge_zero_adjustments():
    out = calculate(_flat(pv_chg_vol=0.0))
    assert all(v == pytest.approx(0.0) for v in out.goo_lost_volume)
    assert all(v == pytest.approx(0.0) for v in out.goo_revenue_lost)
    assert all(v == pytest.approx(0.0) for v in out.export_tariff_addback)


# ---------------------------------------------------------------------------
# Section F — Multimarket
# ---------------------------------------------------------------------------

def test_f_multimarket_zero_when_pct_zero():
    out = calculate(_flat(multi_pct=0.0))
    assert all(v == pytest.approx(0.0) for v in out.multimarket_revenue)


def test_f_multimarket_fraction_of_base():
    out = calculate(_flat(
        dis_vol=500.0, dis_price=800.0, inflation_rate=0.0,
        grid_chg_vol=300.0, grid_chg_price=400.0,
        pv_chg_vol=0.0, pv_chg_price=0.0,
        multi_pct=0.10,
    ))
    for p in range(24):
        assert out.multimarket_revenue[p] == pytest.approx(
            0.10 * out.bess_net_ex_multimarket[p]
        )


def test_f_bess_net_ex_multi_formula():
    """bess_net_ex_multi = discharge_rev − charging − import − export + adjustments"""
    out = calculate(_flat(
        inflation_rate=0.0,
        tso_export_tariff=_vt(2.0),
        feed_in_tariff=_vt(1.0),
    ))
    for p in range(24):
        expected = (
            out.discharge_revenue[p]
            - out.total_charging_cost[p]
            - out.total_import_production_costs[p]
            - out.total_export_production_costs[p]
            + out.total_system_adjustments[p]
        )
        assert out.bess_net_ex_multimarket[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Section G — Summary
# ---------------------------------------------------------------------------

def test_g_gross_revenue_is_discharge_plus_multimarket():
    out = calculate(_flat(multi_pct=0.05))
    for p in range(24):
        assert out.gross_revenue[p] == pytest.approx(
            out.discharge_revenue[p] + out.multimarket_revenue[p]
        )


def test_g_total_costs_sums_components():
    out = calculate(_flat(
        inflation_rate=0.0,
        feed_in_tariff=_vt(2.0),
        tso_export_tariff=_vt(1.0),
    ))
    for p in range(24):
        expected = (
            out.total_charging_cost[p]
            + out.total_import_production_costs[p]
            + out.total_export_production_costs[p]
            - out.total_system_adjustments[p]
        )
        assert out.total_costs[p] == pytest.approx(expected)


def test_g_net_revenue_is_gross_minus_costs():
    out = calculate(_flat())
    for p in range(24):
        assert out.net_revenue[p] == pytest.approx(
            out.gross_revenue[p] - out.total_costs[p]
        )


def test_g_net_equals_bess_net_plus_multi():
    """net_revenue = bess_net_ex_multi + multimarket_revenue"""
    out = calculate(_flat(multi_pct=0.05))
    for p in range(24):
        assert out.net_revenue[p] == pytest.approx(
            out.bess_net_ex_multimarket[p] + out.multimarket_revenue[p]
        )


def test_g_annual_sums_monthly():
    out = calculate(_flat(periods=24))
    assert out.annual_net_revenue[0] == pytest.approx(sum(out.net_revenue[:12]))
    assert out.annual_net_revenue[1] == pytest.approx(sum(out.net_revenue[12:]))


def test_g_lifetime_total():
    out = calculate(_flat())
    assert out.total_net_revenue == pytest.approx(sum(out.net_revenue))


def test_g_no_costs_net_equals_discharge():
    """No tariffs, no charging prices → net = discharge revenue."""
    out = calculate(_flat(
        grid_chg_price=0.0, pv_chg_price=0.0,
        goo_price=0.0, multi_pct=0.0,
        inflation_rate=0.0,
    ))
    for p in range(24):
        assert out.net_revenue[p] == pytest.approx(out.discharge_revenue[p])


# ---------------------------------------------------------------------------
# Integration: Viuf BESS proxy
# ---------------------------------------------------------------------------

def test_viuf_bess_proxy():
    """
    Proxy for Viuf BESS: 100 MW / 400 MWh, 475 months.
    Validate: result is an Outputs, lengths correct, total net revenue in plausible range.
    """
    periods = 475
    out = calculate(Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        inflation_rate=0.025,
        power_MW=100.0,
        energy_MWh_installed=400.0,
        round_trip_efficiency=0.85,
        discharge_volume_MWh=[3_000.0] * periods,
        discharge_price_DKK_per_MWh=[900.0] * periods,
        grid_charge_volume_MWh=[2_000.0] * periods,
        grid_charge_price_DKK_per_MWh=[350.0] * periods,
        pv_charge_volume_MWh=[1_500.0] * periods,
        pv_charge_price_DKK_per_MWh=[380.0] * periods,
        goo_price_DKK_per_MWh=[15.0] * periods,
        multimarket_pct=[0.05] * periods,
        feed_in_tariff=VariableTariff(rate_DKK_per_MWh=3.0, inflation_start_year=2026),
        net_loss_pct=0.02,
        net_loss_markup_DKK_per_MWh=5.0,
        system_tariff=SystemTariff(
            tier1_threshold_MWh_annual=20_000.0,
            tier1_rate_DKK_per_MWh=6.0,
            tier2_rate_DKK_per_MWh=3.0,
            inflation_start_year=2026,
        ),
        grid_import_fee_DKK_per_MW_annual=500.0,
        tso_export_tariff=VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2026),
        dso_export_tariff=VariableTariff(rate_DKK_per_MWh=1.0, inflation_start_year=2026),
        nordpool_export_tariff=VariableTariff(rate_DKK_per_MWh=0.5, inflation_start_year=2026),
    ))
    assert isinstance(out, Outputs)
    assert len(out.net_revenue) == 475
    assert out.total_net_revenue > 100_000.0    # DKKk — plausible floor
    assert out.total_net_revenue < 10_000_000.0  # DKKk — plausible ceiling


def test_viuf_bess_system_tariff_tier_break():
    """Monthly grid import of 2,000 MWh: tier crosses at month 10 (20,000/2,000=10)."""
    periods = 24
    st = SystemTariff(
        tier1_threshold_MWh_annual=20_000.0,
        tier1_rate_DKK_per_MWh=6.0,
        tier2_rate_DKK_per_MWh=3.0,
    )
    out = calculate(_flat(grid_chg_vol=2_000.0, inflation_rate=0.0, system_tariff=st))
    # Months 0-9: tier1 (2000 × 6 / 1000 = 12 DKKk)
    for p in range(10):
        assert out.system_tariff_cost[p] == pytest.approx(12.0)
    # Month 10+: tier2 (2000 × 3 / 1000 = 6 DKKk)
    for p in range(10, 12):
        assert out.system_tariff_cost[p] == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "dis_start_factor": "B5", "inf_rate": "B6", "year": "K3",
        "dis_inf_start": "B7", "dis_price_raw": "K20", "dis_factor": "K$5",
        "dis_vol": "K21", "dis_price_inf": "K22",
        "chg_start_factor": "B8", "chg_inf_start": "B9",
        "grid_price_raw": "K30", "chg_factor": "K$10",
        "pv_price_raw": "K31", "grid_vol": "K32", "grid_price_inf": "K33",
        "pv_vol": "K34", "pv_price_inf": "K35",
        "feed_in_rate": "B10", "net_loss_pct": "B11", "markup": "B12",
        "power_mw": "B13", "import_fee_rate": "B14",
        "tso_rate": "B15", "tso_factor": "K$15",
        "dso_rate": "B16", "dso_factor": "K$16",
        "np_rate": "B17", "np_factor": "K$17",
        "goo_lost_vol": "K40", "goo_price": "K41",
        "tso_rate_inf": "K42", "dso_rate_inf": "K43", "np_rate_inf": "K44",
        "rte": "B18",
        "dis_rev": "K50", "chg_cost": "K51", "import_costs": "K52",
        "export_costs": "K53", "sys_adj": "K54",
        "multi_pct": "K55", "bess_net_base": "K56",
        "multi_rev": "K57", "gross_rev": "K58", "total_costs": "K59",
        "tolling_mw": "B60", "tolling_price": "B61", "tolling_factor": "C60",
        "tolling_rev": "K60",
        # FCAS refs
        "fcas_reg_price": "K70", "fcas_con_price": "K71",
        "fcas_reg": "K72", "fcas_con": "K73", "fcas_total": "K74",
        # VTA refs
        "vta_coloc_pct": "K80", "vta_cap_pct": "K81", "vta_deg_pct": "K82",
        "vta_tx_pct": "K83", "vta_vol": "K84", "vta_price": "K85",
        "vta_coloc": "K86", "vta_cap": "K87", "vta_deg": "K88", "vta_tx": "K89",
        "vta_total": "K90",
    }
    formulas = get_excel_formulas(refs)
    for key in (
        "discharge_indexation_factor", "discharge_price_inflated", "discharge_revenue",
        "charge_indexation_factor", "grid_charge_price_inflated", "pv_charge_price_inflated",
        "grid_charging_cost", "pv_charging_cost",
        "feed_in_tariff_cost", "net_loss_tariff_cost", "grid_import_fee",
        "tso_export_cost", "dso_export_cost", "nordpool_export_cost",
        "goo_lost_volume", "goo_revenue_lost", "export_tariff_addback",
        "bess_net_ex_multimarket", "multimarket_revenue",
        "tolling_revenue",
        "fcas_regulation_revenue", "fcas_contingency_revenue", "fcas_total_revenue",
        "vta_colocation_compensation", "vta_capacity_compensation",
        "vta_degradation_compensation", "vta_transmission_compensation",
        "vta_total_compensation",
        "gross_revenue", "total_costs", "net_revenue",
    ):
        assert key in formulas, f"Missing formula key: {key}"
        assert formulas[key].startswith("="), f"Formula {key!r} doesn't start with '='"


# ---------------------------------------------------------------------------
# H. Tolling agreement
# ---------------------------------------------------------------------------

def _tolling_inputs(
    periods=24,
    contracted_mw=50.0,
    price_per_mw_year=100_000.0,
    start_period=0,
    end_period=23,
    inflation_rate=0.0,
    inflation_start_year=2025,
    **kwargs,
):
    return _flat(
        periods=periods,
        tolling_active=True,
        tolling_contracted_mw=contracted_mw,
        tolling_price_DKK_per_mw_per_year=price_per_mw_year,
        tolling_start_period=start_period,
        tolling_end_period=end_period,
        tolling_inflation_rate=inflation_rate,
        tolling_inflation_start_year=inflation_start_year,
        **kwargs,
    )


def test_tolling_inactive_gives_zero():
    out = calculate(_flat())
    assert all(v == pytest.approx(0.0) for v in out.tolling_revenue)


def test_tolling_revenue_basic():
    """50 MW × 100,000 DKK/MW/yr / 12 / 1000 = 416.667 DKKk/month."""
    out = calculate(_tolling_inputs())
    expected = 50.0 * 100_000.0 / 12.0 / 1000.0
    for p in range(24):
        assert out.tolling_revenue[p] == pytest.approx(expected)


def test_tolling_only_in_window():
    """Tolling revenue only between start and end periods."""
    out = calculate(_tolling_inputs(periods=36, start_period=6, end_period=17))
    for p in range(6):
        assert out.tolling_revenue[p] == pytest.approx(0.0)
    for p in range(6, 18):
        assert out.tolling_revenue[p] > 0
    for p in range(18, 36):
        assert out.tolling_revenue[p] == pytest.approx(0.0)


def test_tolling_added_to_gross_revenue():
    """Gross revenue should include tolling."""
    out_no = calculate(_flat())
    out_yes = calculate(_tolling_inputs())
    for p in range(24):
        assert out_yes.gross_revenue[p] > out_no.gross_revenue[p]
        diff = out_yes.gross_revenue[p] - out_no.gross_revenue[p]
        assert diff == pytest.approx(out_yes.tolling_revenue[p])


def test_tolling_added_to_net_revenue():
    """Tolling increases net revenue (it's pure revenue, no associated cost)."""
    out_no = calculate(_flat())
    out_yes = calculate(_tolling_inputs())
    assert out_yes.total_net_revenue > out_no.total_net_revenue


def test_tolling_with_inflation():
    """Tolling fee inflates year-over-year."""
    out = calculate(_tolling_inputs(
        periods=24, start_period=0, end_period=23,
        inflation_rate=0.05, inflation_start_year=2025,
    ))
    # Year 2 (periods 12-23) should have higher tolling than year 1 (periods 0-11)
    assert out.tolling_revenue[12] > out.tolling_revenue[0]
    # Factor should be 1.05× higher
    assert out.tolling_revenue[12] / out.tolling_revenue[0] == pytest.approx(1.05, rel=1e-6)


def test_tolling_zero_contracted_mw():
    """Active but 0 MW → no revenue."""
    out = calculate(_tolling_inputs(contracted_mw=0.0))
    assert all(v == pytest.approx(0.0) for v in out.tolling_revenue)


def test_tolling_validation_end_exceeds_periods():
    with pytest.raises(ValueError, match="tolling_end_period"):
        _tolling_inputs(periods=12, end_period=12)


def test_tolling_validation_start_after_end():
    with pytest.raises(ValueError, match="tolling_start_period"):
        _tolling_inputs(start_period=10, end_period=5)


def test_tolling_array_length():
    n = 36
    out = calculate(_tolling_inputs(periods=n, end_period=n - 1))
    assert len(out.tolling_revenue) == n


def test_tolling_formula_in_excel():
    refs = {
        "dis_start_factor": "B5", "inf_rate": "B6", "year": "C3",
        "dis_inf_start": "B7", "dis_price_raw": "C10", "dis_factor": "C8",
        "dis_vol": "C6", "dis_price_inf": "C11",
        "chg_start_factor": "B15", "chg_inf_start": "B16",
        "grid_price_raw": "C14", "chg_factor": "C13",
        "pv_price_raw": "C15", "grid_vol": "C12",
        "pv_vol": "C16", "grid_price_inf": "C14",
        "pv_price_inf": "C15", "feed_in_rate": "B20",
        "net_loss_pct": "B21", "markup": "B22",
        "power_mw": "B3", "import_fee_rate": "B25",
        "tso_rate": "B30", "tso_factor": "C30",
        "dso_rate": "B31", "dso_factor": "C31",
        "np_rate": "B32", "np_factor": "C32",
        "rte": "B4", "goo_lost_vol": "C40", "goo_price": "C41",
        "tso_rate_inf": "C45", "dso_rate_inf": "C46", "np_rate_inf": "C47",
        "dis_rev": "K50", "chg_cost": "K51", "import_costs": "K52",
        "export_costs": "K53", "sys_adj": "K54",
        "multi_pct": "K55", "bess_net_base": "K56",
        "multi_rev": "K57", "gross_rev": "K58", "total_costs": "K59",
        "tolling_mw": "B60", "tolling_price": "B61", "tolling_factor": "C60",
        "tolling_rev": "K60",
        "fcas_reg_price": "K70", "fcas_con_price": "K71",
        "fcas_reg": "K72", "fcas_con": "K73", "fcas_total": "K74",
        "vta_coloc_pct": "K80", "vta_cap_pct": "K81", "vta_deg_pct": "K82",
        "vta_tx_pct": "K83", "vta_vol": "K84", "vta_price": "K85",
        "vta_coloc": "K86", "vta_cap": "K87", "vta_deg": "K88", "vta_tx": "K89",
        "vta_total": "K90",
    }
    formulas = get_excel_formulas(refs)
    assert "tolling_revenue" in formulas
    assert formulas["tolling_revenue"].startswith("=")


# ---------------------------------------------------------------------------
# External mode
# ---------------------------------------------------------------------------

def test_external_mode_returns_external_values():
    """External mode passes through external BESS revenue curve."""
    n = 12
    ext_rev = [500.0] * n
    inp = Inputs(
        periods=n,
        start_year=2025,
        start_month=1,
        inflation_rate=0.0,
        power_MW=100.0,
        energy_MWh_installed=400.0,
        round_trip_efficiency=0.85,
        discharge_volume_MWh=[0.0] * n,
        discharge_price_DKK_per_MWh=[0.0] * n,
        grid_charge_volume_MWh=[0.0] * n,
        grid_charge_price_DKK_per_MWh=[0.0] * n,
        pv_charge_volume_MWh=[0.0] * n,
        pv_charge_price_DKK_per_MWh=[0.0] * n,
        goo_price_DKK_per_MWh=[0.0] * n,
        multimarket_pct=[0.0] * n,
        external_mode=True,
        external_bess_revenue_DKKk=ext_rev,
    )
    out = calculate(inp)
    assert isinstance(out, Outputs)
    assert len(out.net_revenue) == n
    for p in range(n):
        assert out.net_revenue[p] == pytest.approx(500.0)
        assert out.gross_revenue[p] == pytest.approx(500.0)
        assert out.total_costs[p] == pytest.approx(0.0)
        assert out.discharge_revenue[p] == pytest.approx(0.0)
    assert out.total_net_revenue == pytest.approx(500.0 * n)


def test_external_mode_false_unchanged():
    """When external_mode=False, normal internal calc runs."""
    out = calculate(_flat(dis_vol=500.0, dis_price=800.0, inflation_rate=0.0))
    expected = 500.0 * 800.0 / 1000.0
    assert all(v == pytest.approx(expected) for v in out.discharge_revenue)
    # Confirm default
    inp = _flat()
    assert inp.external_mode is False
    assert inp.external_bess_revenue_DKKk == []


def test_external_mode_validation():
    """External mode validates external array length."""
    with pytest.raises(ValueError, match="external_bess_revenue_DKKk"):
        Inputs(
            periods=12,
            start_year=2025,
            start_month=1,
            power_MW=100.0,
            energy_MWh_installed=400.0,
            round_trip_efficiency=0.85,
            discharge_volume_MWh=[0.0] * 12,
            discharge_price_DKK_per_MWh=[0.0] * 12,
            grid_charge_volume_MWh=[0.0] * 12,
            grid_charge_price_DKK_per_MWh=[0.0] * 12,
            pv_charge_volume_MWh=[0.0] * 12,
            pv_charge_price_DKK_per_MWh=[0.0] * 12,
            goo_price_DKK_per_MWh=[0.0] * 12,
            multimarket_pct=[0.0] * 12,
            external_mode=True,
            external_bess_revenue_DKKk=[1.0] * 10,  # wrong length
        )


# ============================================================================
# DEGRADATION CURVES (Session 1 — UC Model Learnings)
# ============================================================================

# Real CATL 20-year curves from Upper Calliope FID model
_RTE_CURVE = [0.820, 0.815, 0.808, 0.804, 0.801, 0.800, 0.799, 0.797,
              0.796, 0.795, 0.794, 0.793, 0.791, 0.790, 0.789, 0.788,
              0.787, 0.786, 0.785, 0.784]

_CAP_CURVE = [0.948, 0.928, 0.901, 0.881, 0.863, 0.844, 0.828,
              0.813, 0.796, 0.782, 0.769, 0.754, 0.742, 0.730,
              0.718, 0.704, 0.693, 0.682, 0.671, 0.661]


def _make_degradation_inputs(n=240, rte_curve=None, capacity_curve=None):
    """Build a 20-year BESS config for degradation curve testing."""
    return Inputs(
        periods=n, start_year=2026, start_month=1,
        power_MW=28.0, energy_MWh_installed=112.0,
        round_trip_efficiency=0.82,
        discharge_volume_MWh=[100.0] * n,
        discharge_price_DKK_per_MWh=[50.0] * n,
        grid_charge_volume_MWh=[50.0] * n,
        grid_charge_price_DKK_per_MWh=[40.0] * n,
        pv_charge_volume_MWh=[60.0] * n,
        pv_charge_price_DKK_per_MWh=[30.0] * n,
        goo_price_DKK_per_MWh=[5.0] * n,
        multimarket_pct=[0.0] * n,
        rte_curve=rte_curve,
        capacity_curve=capacity_curve,
    )


def test_degradation_no_curves_uses_flat_rte():
    """Without curves, effective_rte equals flat round_trip_efficiency."""
    inp = _make_degradation_inputs()
    out = calculate(inp)
    assert all(v == 0.82 for v in out.effective_rte)
    assert all(v == 1.0 for v in out.effective_capacity_factor)


def test_degradation_rte_curve_overrides_flat():
    """RTE curve replaces flat value; year 1 = 0.820, year 20 = 0.784."""
    out = calculate(_make_degradation_inputs(rte_curve=_RTE_CURVE))
    # Year 1 = periods 0-11 → RTE 0.820
    assert abs(out.effective_rte[0] - 0.820) < 1e-6
    # Year 20 = periods 228-239 → RTE 0.784
    assert abs(out.effective_rte[239] - 0.784) < 1e-6


def test_degradation_rte_affects_goo_losses():
    """Higher RTE loss in year 20 → more GoO volume lost."""
    out_flat = calculate(_make_degradation_inputs())
    out_curve = calculate(_make_degradation_inputs(rte_curve=_RTE_CURVE))
    # Year 1: curve RTE (0.82) == flat RTE (0.82) → same losses
    assert abs(out_flat.goo_lost_volume[0] - out_curve.goo_lost_volume[0]) < 0.01
    # Year 20: curve RTE (0.784) < flat (0.82) → more losses with curve
    assert out_curve.goo_lost_volume[239] > out_flat.goo_lost_volume[239]


def test_degradation_capacity_curve_scales_discharge():
    """Capacity curve reduces discharge volume; year 20 materially lower."""
    out_flat = calculate(_make_degradation_inputs())
    out_cap = calculate(_make_degradation_inputs(capacity_curve=_CAP_CURVE))
    # Year 1: capacity 0.948 → discharge = 100 * 0.948 = 94.8
    assert abs(out_cap.discharge_volume[0] - 100.0 * 0.948) < 0.01
    # Year 20: capacity 0.661 → discharge = 100 * 0.661 = 66.1
    assert abs(out_cap.discharge_volume[239] - 100.0 * 0.661) < 0.01
    # Flat has full 100.0
    assert abs(out_flat.discharge_volume[0] - 100.0) < 0.01


def test_degradation_capacity_reduces_revenue():
    """Capacity degradation → lower lifetime revenue."""
    out_flat = calculate(_make_degradation_inputs())
    out_cap = calculate(_make_degradation_inputs(capacity_curve=_CAP_CURVE))
    assert out_cap.total_net_revenue < out_flat.total_net_revenue


def test_degradation_both_curves_combined():
    """Both curves together produce lower revenue than either alone."""
    out_flat = calculate(_make_degradation_inputs())
    out_rte = calculate(_make_degradation_inputs(rte_curve=_RTE_CURVE))
    out_cap = calculate(_make_degradation_inputs(capacity_curve=_CAP_CURVE))
    out_both = calculate(_make_degradation_inputs(
        rte_curve=_RTE_CURVE, capacity_curve=_CAP_CURVE
    ))
    assert out_both.total_net_revenue < out_rte.total_net_revenue
    assert out_both.total_net_revenue < out_cap.total_net_revenue
    assert out_both.total_net_revenue < out_flat.total_net_revenue


def test_degradation_short_curve_holds_last_value():
    """If curve is shorter than project years, last value is held."""
    short_rte = [0.82, 0.80]  # only 2 years
    out = calculate(_make_degradation_inputs(rte_curve=short_rte))
    # Year 3+ should use 0.80 (last value)
    assert abs(out.effective_rte[24] - 0.80) < 1e-6
    assert abs(out.effective_rte[239] - 0.80) < 1e-6


def test_degradation_capacity_factor_output_matches_curve():
    """Output effective_capacity_factor reflects the input curve."""
    out = calculate(_make_degradation_inputs(capacity_curve=_CAP_CURVE))
    assert abs(out.effective_capacity_factor[0] - 0.948) < 1e-6
    assert abs(out.effective_capacity_factor[239] - 0.661) < 1e-6


def test_degradation_validation_rte_curve_empty():
    with pytest.raises(ValueError, match="rte_curve must not be empty"):
        _make_degradation_inputs(rte_curve=[])


def test_degradation_validation_rte_curve_invalid_values():
    with pytest.raises(ValueError, match="rte_curve values must be in"):
        _make_degradation_inputs(rte_curve=[1.5])


def test_degradation_validation_capacity_curve_empty():
    with pytest.raises(ValueError, match="capacity_curve must not be empty"):
        _make_degradation_inputs(capacity_curve=[])


def test_degradation_validation_capacity_curve_invalid():
    with pytest.raises(ValueError, match="capacity_curve values must be in"):
        _make_degradation_inputs(capacity_curve=[0.0])


# ---------------------------------------------------------------------------
# VTA Compensation tests
# ---------------------------------------------------------------------------

def _vta_inputs(n=12, **overrides):
    """Build minimal Inputs with VTA fields."""
    base = dict(
        periods=n, start_year=2028, start_month=1,
        power_MW=100.0, energy_MWh_installed=400.0, round_trip_efficiency=0.85,
        inflation_rate=0.0,
        discharge_volume_MWh=[1000.0] * n,
        discharge_price_DKK_per_MWh=[100.0] * n,
        grid_charge_volume_MWh=[600.0] * n,
        grid_charge_price_DKK_per_MWh=[50.0] * n,
        pv_charge_volume_MWh=[400.0] * n,
        pv_charge_price_DKK_per_MWh=[40.0] * n,
        goo_price_DKK_per_MWh=[5.0] * n,
        multimarket_pct=[0.0] * n,
    )
    base.update(overrides)
    return Inputs(**base)


def test_vta_inactive_returns_zeros():
    out = calculate(_vta_inputs(vta_active=False))
    assert all(v == 0.0 for v in out.vta_total_compensation)
    assert all(v == 0.0 for v in out.vta_colocation_compensation)


def test_vta_colocation_only():
    n = 12
    out = calculate(_vta_inputs(
        vta_active=True,
        vta_contracted_discharge_MWh=[2000.0] * n,
        vta_discharge_price_per_MWh=[100.0] * n,
        vta_colocation_active=True,
        vta_colocation_shortfall_pct=[0.05] * n,
    ))
    # -0.05 * 2000 * 100 / 1000 = -10.0 per period
    assert out.vta_colocation_compensation[0] == pytest.approx(-10.0)
    assert all(v == 0.0 for v in out.vta_capacity_compensation)


def test_vta_capacity_shortfall():
    n = 12
    out = calculate(_vta_inputs(
        vta_active=True,
        vta_contracted_discharge_MWh=[1000.0] * n,
        vta_discharge_price_per_MWh=[200.0] * n,
        vta_capacity_shortfall_active=True,
        vta_capacity_shortfall_pct=[0.03] * n,
    ))
    # -0.03 * 1000 * 200 / 1000 = -6.0
    assert out.vta_capacity_compensation[0] == pytest.approx(-6.0)


def test_vta_transmission_only():
    n = 12
    out = calculate(_vta_inputs(
        vta_active=True,
        vta_contracted_discharge_MWh=[500.0] * n,
        vta_discharge_price_per_MWh=[80.0] * n,
        vta_transmission_active=True,
        vta_transmission_loss_pct=[0.10] * n,
    ))
    # -0.10 * 500 * 80 / 1000 = -4.0
    assert out.vta_transmission_compensation[0] == pytest.approx(-4.0)


def test_vta_all_layers_combined():
    n = 12
    out = calculate(_vta_inputs(
        vta_active=True,
        vta_contracted_discharge_MWh=[1000.0] * n,
        vta_discharge_price_per_MWh=[100.0] * n,
        vta_colocation_active=True,
        vta_colocation_shortfall_pct=[0.02] * n,
        vta_capacity_shortfall_active=True,
        vta_capacity_shortfall_pct=[0.03] * n,
        vta_degradation_active=True,
        vta_degradation_shortfall_pct=[0.01] * n,
        vta_transmission_active=True,
        vta_transmission_loss_pct=[0.04] * n,
    ))
    # Total: -(0.02+0.03+0.01+0.04) * 1000 * 100 / 1000 = -10.0
    assert out.vta_total_compensation[0] == pytest.approx(-10.0)


def test_vta_deducts_from_net_revenue():
    n = 12
    out_no_vta = calculate(_vta_inputs())
    out_vta = calculate(_vta_inputs(
        vta_active=True,
        vta_contracted_discharge_MWh=[1000.0] * n,
        vta_discharge_price_per_MWh=[100.0] * n,
        vta_colocation_active=True,
        vta_colocation_shortfall_pct=[0.05] * n,
    ))
    # VTA compensation is negative, so net_revenue should be lower
    assert sum(out_vta.net_revenue) < sum(out_no_vta.net_revenue)


def test_vta_tolling_revenue_unchanged():
    n = 12
    out_with_vta = calculate(_vta_inputs(
        tolling_active=True,
        tolling_contracted_mw=50.0,
        tolling_price_DKK_per_mw_per_year=100_000.0,
        tolling_start_period=0,
        tolling_end_period=11,
        vta_active=True,
        vta_contracted_discharge_MWh=[1000.0] * n,
        vta_discharge_price_per_MWh=[100.0] * n,
        vta_colocation_active=True,
        vta_colocation_shortfall_pct=[0.05] * n,
    ))
    out_no_vta = calculate(_vta_inputs(
        tolling_active=True,
        tolling_contracted_mw=50.0,
        tolling_price_DKK_per_mw_per_year=100_000.0,
        tolling_start_period=0,
        tolling_end_period=11,
    ))
    # Tolling revenue should NOT be reduced by VTA compensation
    assert out_with_vta.tolling_revenue[0] == pytest.approx(out_no_vta.tolling_revenue[0])


def test_vta_validation_missing_volume():
    with pytest.raises(ValueError, match="vta_contracted_discharge_MWh"):
        _vta_inputs(
            vta_active=True,
            vta_contracted_discharge_MWh=[1000.0] * 8,  # wrong length (need 12)
            vta_discharge_price_per_MWh=[100.0] * 12,
        )


# ---------------------------------------------------------------------------
# FCAS Revenue tests
# ---------------------------------------------------------------------------

def test_fcas_inactive_returns_zeros():
    out = calculate(_vta_inputs(fcas_active=False))
    assert all(v == 0.0 for v in out.fcas_total_revenue)


def test_fcas_regulation_only():
    n = 12
    out = calculate(_vta_inputs(
        fcas_active=True,
        fcas_regulation_price_per_MWh=[20.0] * n,
        fcas_contingency_price_per_MWh=[0.0] * n,
    ))
    # dis_volume = 1000 (from _vta_inputs), reg = 1000 * 20 / 1000 = 20.0
    assert out.fcas_regulation_revenue[0] == pytest.approx(20.0)
    assert out.fcas_contingency_revenue[0] == pytest.approx(0.0)


def test_fcas_both_streams():
    n = 12
    out = calculate(_vta_inputs(
        fcas_active=True,
        fcas_regulation_price_per_MWh=[15.0] * n,
        fcas_contingency_price_per_MWh=[10.0] * n,
    ))
    # reg = 1000*15/1000=15, con = 1000*10/1000=10, total = 25
    assert out.fcas_total_revenue[0] == pytest.approx(25.0)


def test_fcas_adds_to_gross_revenue():
    n = 12
    out_no_fcas = calculate(_vta_inputs())
    out_fcas = calculate(_vta_inputs(
        fcas_active=True,
        fcas_regulation_price_per_MWh=[20.0] * n,
        fcas_contingency_price_per_MWh=[10.0] * n,
    ))
    diff = sum(out_fcas.gross_revenue) - sum(out_no_fcas.gross_revenue)
    assert diff == pytest.approx(sum(out_fcas.fcas_total_revenue), rel=1e-6)


def test_fcas_external_mode_returns_zeros():
    n = 12
    out = calculate(Inputs(
        periods=n, start_year=2028, start_month=1,
        power_MW=100.0, energy_MWh_installed=400.0, round_trip_efficiency=0.85,
        discharge_volume_MWh=[0.0] * n,
        discharge_price_DKK_per_MWh=[0.0] * n,
        grid_charge_volume_MWh=[0.0] * n,
        grid_charge_price_DKK_per_MWh=[0.0] * n,
        pv_charge_volume_MWh=[0.0] * n,
        pv_charge_price_DKK_per_MWh=[0.0] * n,
        goo_price_DKK_per_MWh=[0.0] * n,
        multimarket_pct=[0.0] * n,
        external_mode=True,
        external_bess_revenue_DKKk=[100.0] * n,
        fcas_active=True,
        fcas_regulation_price_per_MWh=[20.0] * n,
        fcas_contingency_price_per_MWh=[10.0] * n,
    ))
    assert all(v == 0.0 for v in out.fcas_total_revenue)


def test_fcas_validation():
    with pytest.raises(ValueError, match="fcas_regulation_price_per_MWh"):
        _vta_inputs(
            fcas_active=True,
            fcas_regulation_price_per_MWh=[20.0] * 8,  # wrong length
            fcas_contingency_price_per_MWh=[10.0] * 12,
        )


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_existing_inputs_unchanged():
    """Old-style inputs (no VTA/FCAS fields) still work; new outputs are zero."""
    out = calculate(_flat())
    assert len(out.vta_total_compensation) == 24
    assert all(v == 0.0 for v in out.vta_total_compensation)
    assert len(out.fcas_total_revenue) == 24
    assert all(v == 0.0 for v in out.fcas_total_revenue)
    # Gross and net revenue unchanged from pre-VTA/FCAS behavior
    for p in range(24):
        assert out.gross_revenue[p] == pytest.approx(
            out.discharge_revenue[p] + out.multimarket_revenue[p] + out.tolling_revenue[p]
        )
