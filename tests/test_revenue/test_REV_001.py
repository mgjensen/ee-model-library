"""Tests for REV_001 — PV revenue module, sub-sections A–H."""

import pytest
from modules.revenue.REV_001 import (
    Inputs,
    Outputs,
    PPASlot,
    PPASlotOutput,
    VariableTariff,
    calculate,
    get_excel_formulas,
    _indexation_factor,
    _variable_cost_series,
    _year_groups,
    _calculate_ppa_slot,
    _cal_to_period_map,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    production=1_000.0,      # MWh/month flat
    wholesale=400.0,          # DKK/MWh flat
    capture=380.0,            # DKK/MWh flat (95% of wholesale)
    goo_price=10.0,           # DKK/MWh
    inflation_rate=0.0,
    ppa_slots=None,
    tso=None, dso=None, nordpool=None, imbalance=None,
    ppa_management_rate=0.0,
) -> Inputs:
    n = periods
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        inflation_rate=inflation_rate,
        net_production_MWh=[production] * n,
        wholesale_price_DKK_per_MWh=[wholesale] * n,
        capture_price_DKK_per_MWh=[capture] * n,
        goo_price_DKK_per_MWh=[goo_price] * n,
        ppa_slots=ppa_slots or [PPASlot() for _ in range(5)],
        tso_tariff=tso or VariableTariff(),
        dso_tariff=dso or VariableTariff(),
        nordpool_tariff=nordpool or VariableTariff(),
        imbalance_tariff=imbalance or VariableTariff(),
        ppa_management_rate_DKK_per_MWh=ppa_management_rate,
    )


def _active_ppa(periods, price_DKK_per_MWh, volume_MWh, **kwargs) -> PPASlot:
    return PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=price_DKK_per_MWh,
        contracted_volume=[volume_MWh] * periods,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Section A — Production pass-through
# ---------------------------------------------------------------------------

def test_a_production_passthrough():
    out = calculate(_flat_inputs(production=1_234.5))
    assert all(v == pytest.approx(1_234.5) for v in out.net_production_MWh)


# ---------------------------------------------------------------------------
# Section B — Price indexation
# ---------------------------------------------------------------------------

def test_b_no_inflation_factor_is_one():
    out = calculate(_flat_inputs(inflation_rate=0.0))
    assert all(f == pytest.approx(1.0) for f in out.price_indexation_factor)


def test_b_inflation_increases_factor_in_year2():
    out = calculate(_flat_inputs(periods=24, inflation_rate=0.05))
    assert out.price_indexation_factor[12] == pytest.approx(1.05)


def test_b_wholesale_inflated_year1():
    out = calculate(_flat_inputs(wholesale=400.0, inflation_rate=0.05))
    assert out.wholesale_inflated[0] == pytest.approx(400.0)


def test_b_wholesale_inflated_year2():
    out = calculate(_flat_inputs(wholesale=400.0, inflation_rate=0.05))
    assert out.wholesale_inflated[12] == pytest.approx(400.0 * 1.05)


def test_b_capture_inflated_matches_factor():
    out = calculate(_flat_inputs(capture=380.0, inflation_rate=0.025))
    for p in range(24):
        assert out.capture_inflated[p] == pytest.approx(
            380.0 * out.price_indexation_factor[p]
        )


# ---------------------------------------------------------------------------
# Section C — Spot revenue
# ---------------------------------------------------------------------------

def test_c_spot_revenue_no_inflation():
    """spot = production × capture / 1000"""
    out = calculate(_flat_inputs(production=1_000.0, capture=400.0, inflation_rate=0.0))
    expected = 1_000.0 * 400.0 / 1000.0  # = 400 DKKk/month
    assert all(v == pytest.approx(expected) for v in out.spot_revenue)


def test_c_spot_revenue_zero_production():
    out = calculate(_flat_inputs(production=0.0))
    assert all(v == pytest.approx(0.0) for v in out.spot_revenue)


def test_c_spot_revenue_inflated():
    """Capture price is inflated before multiplying production."""
    out = calculate(_flat_inputs(production=1_000.0, capture=400.0, inflation_rate=0.12))
    assert out.spot_revenue[12] == pytest.approx(1_000.0 * 400.0 * 1.12 / 1000.0)


# ---------------------------------------------------------------------------
# Section D — PPA slots
# ---------------------------------------------------------------------------

def test_d_inactive_slot_all_zeros():
    out = calculate(_flat_inputs())
    for slot in out.ppa:
        assert all(v == 0.0 for v in slot.contracted_volume)
        assert all(v == 0.0 for v in slot.fixed_payment)
        assert all(v == 0.0 for v in slot.net_settlement)
        assert all(v == 0.0 for v in slot.capture_compensation)


def test_d_ppa_fixed_payment():
    """fixed_payment = contracted_volume × ppa_price / 1000"""
    slots = [_active_ppa(24, price_DKK_per_MWh=400.0, volume_MWh=500.0)] + \
            [PPASlot()] * 4
    out = calculate(_flat_inputs(ppa_slots=slots))
    expected = 500.0 * 400.0 / 1000.0  # = 200 DKKk/month
    assert all(v == pytest.approx(expected) for v in out.ppa[0].fixed_payment)


def test_d_ppa_settlement_uses_wholesale():
    """settlement = contracted_volume × wholesale_inflated / 1000"""
    slots = [_active_ppa(24, 400.0, 500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(wholesale=350.0, ppa_slots=slots, inflation_rate=0.0))
    expected = 500.0 * 350.0 / 1000.0  # = 175 DKKk
    assert all(v == pytest.approx(expected) for v in out.ppa[0].settlement)


def test_d_ppa_net_settlement_ppa_above_spot():
    """PPA price > wholesale → positive net settlement."""
    slots = [_active_ppa(24, 400.0, 500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(wholesale=300.0, ppa_slots=slots, inflation_rate=0.0))
    # net = (400 - 300) × 500 / 1000 = 50 DKKk/month
    assert all(v == pytest.approx(50.0) for v in out.ppa[0].net_settlement)


def test_d_ppa_net_settlement_ppa_below_spot():
    """PPA price < wholesale → negative net settlement (seller pays back)."""
    slots = [_active_ppa(24, 300.0, 500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(wholesale=400.0, ppa_slots=slots, inflation_rate=0.0))
    assert all(v < 0 for v in out.ppa[0].net_settlement)


def test_d_ppa_total_revenue_is_net_plus_capture_comp():
    slots = [_active_ppa(24, 400.0, 500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(wholesale=350.0, ppa_slots=slots, inflation_rate=0.0))
    for p in range(24):
        assert out.ppa[0].total_revenue[p] == pytest.approx(
            out.ppa[0].net_settlement[p] + out.ppa[0].capture_compensation[p]
        )


def test_d_ppa_five_slots_independent():
    """Two active slots add independently."""
    slots = (
        [_active_ppa(24, 400.0, 300.0)] +
        [_active_ppa(24, 420.0, 200.0)] +
        [PPASlot()] * 3
    )
    out = calculate(_flat_inputs(wholesale=350.0, ppa_slots=slots, inflation_rate=0.0))
    # slot 0: (400-350)×300/1000 = 15 DKKk
    assert all(v == pytest.approx(15.0) for v in out.ppa[0].net_settlement)
    # slot 1: (420-350)×200/1000 = 14 DKKk
    assert all(v == pytest.approx(14.0) for v in out.ppa[1].net_settlement)


def test_d_total_ppa_volume_sums_slots():
    slots = (
        [_active_ppa(24, 400.0, 300.0)] +
        [_active_ppa(24, 400.0, 200.0)] +
        [PPASlot()] * 3
    )
    out = calculate(_flat_inputs(ppa_slots=slots))
    assert all(v == pytest.approx(500.0) for v in out.total_ppa_contracted_volume)


def test_d_total_ppa_net_settlement_sums_slots():
    slots = (
        [_active_ppa(24, 400.0, 300.0)] +
        [_active_ppa(24, 420.0, 200.0)] +
        [PPASlot()] * 3
    )
    out = calculate(_flat_inputs(wholesale=350.0, ppa_slots=slots, inflation_rate=0.0))
    for p in range(24):
        expected = sum(s.net_settlement[p] for s in out.ppa)
        assert out.total_ppa_net_settlement[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Section D — Capture compensation
# ---------------------------------------------------------------------------

def test_d_capture_comp_triggered_when_below_breakeven():
    """Avg capture < breakeven → compensation in settlement month of following year."""
    # capture=300, breakeven=400 → diff=100, vol=500 MWh/month × 12 = 6,000 MWh/year
    # comp = 100 × 6,000 / 1000 = 600 DKKk settled in Feb 2026 (month 2)
    slot = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=500.0,
        contracted_volume=[500.0] * 24,
        capture_comp_breakeven_DKK_per_MWh=400.0,
        capture_comp_settlement_month=2,
    )
    out = calculate(_flat_inputs(
        periods=24, capture=300.0, wholesale=300.0, inflation_rate=0.0,
        ppa_slots=[slot] + [PPASlot()] * 4,
    ))
    # Expect compensation in period 13 (Feb 2026 = month 2 of year 2)
    assert out.ppa[0].capture_compensation[13] == pytest.approx(600.0)
    # Only one non-zero entry in year 2025 compensation
    assert sum(1 for v in out.ppa[0].capture_compensation[:13] if abs(v) > 1e-9) == 0


def test_d_capture_comp_not_triggered_when_above_breakeven():
    """Avg capture ≥ breakeven → zero compensation."""
    slot = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=500.0,
        contracted_volume=[500.0] * 24,
        capture_comp_breakeven_DKK_per_MWh=300.0,
    )
    out = calculate(_flat_inputs(
        capture=400.0, wholesale=400.0, inflation_rate=0.0,
        ppa_slots=[slot] + [PPASlot()] * 4,
    ))
    assert all(v == pytest.approx(0.0) for v in out.ppa[0].capture_compensation)


def test_d_capture_comp_ceiling_caps_compensation():
    """Compensation capped at ceiling_DKK_per_MWh."""
    # capture=200, breakeven=400, ceiling=50 → use ceiling
    # comp = 50 × 6000 / 1000 = 300 DKKk (not 100 × 6000/1000 = 600)
    slot = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=500.0,
        contracted_volume=[500.0] * 24,
        capture_comp_breakeven_DKK_per_MWh=400.0,
        capture_comp_ceiling_DKK_per_MWh=50.0,
        capture_comp_settlement_month=2,
    )
    out = calculate(_flat_inputs(
        periods=24, capture=200.0, wholesale=200.0, inflation_rate=0.0,
        ppa_slots=[slot] + [PPASlot()] * 4,
    ))
    assert out.ppa[0].capture_compensation[13] == pytest.approx(300.0)


def test_d_capture_comp_out_of_model_range_is_zero():
    """If settlement month falls beyond the model end, no payment recorded."""
    # 12-period model (2025 only); settlement in Feb 2026 = period 13 → doesn't exist
    slot = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=500.0,
        contracted_volume=[500.0] * 12,
        capture_comp_breakeven_DKK_per_MWh=400.0,
        capture_comp_settlement_month=6,  # Jun 2026 = period 17 → outside 12 periods
    )
    out = calculate(_flat_inputs(
        periods=12, capture=300.0, wholesale=300.0, inflation_rate=0.0,
        ppa_slots=[slot] + [PPASlot()] * 4,
    ))
    assert all(v == pytest.approx(0.0) for v in out.ppa[0].capture_compensation)


# ---------------------------------------------------------------------------
# Section E — GoO
# ---------------------------------------------------------------------------

def test_e_goo_volume_no_ppa():
    """No PPA → goo_volume = net_production."""
    out = calculate(_flat_inputs(production=1_000.0))
    assert all(v == pytest.approx(1_000.0) for v in out.goo_volume)


def test_e_goo_volume_with_ppa():
    """GoO volume = production − PPA volume."""
    slots = [_active_ppa(24, 400.0, 300.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(production=1_000.0, ppa_slots=slots))
    assert all(v == pytest.approx(700.0) for v in out.goo_volume)


def test_e_goo_volume_floor_zero():
    """PPA volume > production → goo_volume = 0 (not negative)."""
    slots = [_active_ppa(24, 400.0, 1_500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(production=1_000.0, ppa_slots=slots))
    assert all(v >= 0.0 for v in out.goo_volume)


def test_e_goo_revenue():
    out = calculate(_flat_inputs(production=1_000.0, goo_price=10.0))
    expected = 1_000.0 * 10.0 / 1000.0  # = 10 DKKk/month
    assert all(v == pytest.approx(expected) for v in out.goo_revenue)


def test_e_goo_revenue_zero_goo_price():
    out = calculate(_flat_inputs(goo_price=0.0))
    assert all(v == pytest.approx(0.0) for v in out.goo_revenue)


# ---------------------------------------------------------------------------
# Section F — Production costs
# ---------------------------------------------------------------------------

def test_f_tso_cost_basic():
    tso = VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, tso=tso, inflation_rate=0.0))
    expected = 1_000.0 * 2.0 / 1000.0  # = 2 DKKk
    assert all(v == pytest.approx(expected) for v in out.tso_cost)


def test_f_dso_cost_basic():
    dso = VariableTariff(rate_DKK_per_MWh=1.5, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, dso=dso, inflation_rate=0.0))
    assert all(v == pytest.approx(1.5) for v in out.dso_cost)


def test_f_nordpool_cost_basic():
    np = VariableTariff(rate_DKK_per_MWh=0.5, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, nordpool=np, inflation_rate=0.0))
    assert all(v == pytest.approx(0.5) for v in out.nordpool_cost)


def test_f_tariff_inflation_applied():
    tso = VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, tso=tso, inflation_rate=0.05))
    assert out.tso_cost[12] == pytest.approx(1_000.0 * 2.0 * 1.05 / 1000.0)


def test_f_ppa_management_cost():
    slots = [_active_ppa(24, 400.0, 500.0)] + [PPASlot()] * 4
    out = calculate(_flat_inputs(ppa_slots=slots, ppa_management_rate=1.0))
    expected = 500.0 * 1.0 / 1000.0  # = 0.5 DKKk
    assert all(v == pytest.approx(expected) for v in out.ppa_management_cost)


def test_f_total_production_costs_sums_components():
    tso = VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2025)
    dso = VariableTariff(rate_DKK_per_MWh=1.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, tso=tso, dso=dso, inflation_rate=0.0))
    for p in range(24):
        expected = out.tso_cost[p] + out.dso_cost[p] + out.nordpool_cost[p] + out.ppa_management_cost[p]
        assert out.total_production_costs[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Section G — Balancing
# ---------------------------------------------------------------------------

def test_g_balancing_cost():
    imb = VariableTariff(rate_DKK_per_MWh=3.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, imbalance=imb, inflation_rate=0.0))
    expected = 1_000.0 * 3.0 / 1000.0  # = 3 DKKk
    assert all(v == pytest.approx(expected) for v in out.balancing_cost)


def test_g_balancing_inflation():
    imb = VariableTariff(rate_DKK_per_MWh=3.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(production=1_000.0, imbalance=imb, inflation_rate=0.10))
    assert out.balancing_cost[12] == pytest.approx(1_000.0 * 3.0 * 1.10 / 1000.0)


# ---------------------------------------------------------------------------
# Section H — Summary
# ---------------------------------------------------------------------------

def test_h_gross_revenue_is_spot_plus_ppa_plus_goo():
    out = calculate(_flat_inputs())
    for p in range(24):
        expected = out.spot_revenue[p] + out.total_ppa_revenue[p] + out.goo_revenue[p]
        assert out.gross_revenue[p] == pytest.approx(expected)


def test_h_total_costs_is_production_plus_balancing():
    tso = VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2025)
    imb = VariableTariff(rate_DKK_per_MWh=1.0, inflation_start_year=2025)
    out = calculate(_flat_inputs(tso=tso, imbalance=imb))
    for p in range(24):
        assert out.total_costs[p] == pytest.approx(
            out.total_production_costs[p] + out.balancing_cost[p]
        )


def test_h_net_revenue_is_gross_minus_costs():
    out = calculate(_flat_inputs())
    for p in range(24):
        assert out.net_revenue[p] == pytest.approx(
            out.gross_revenue[p] - out.total_costs[p]
        )


def test_h_no_costs_net_equals_gross():
    out = calculate(_flat_inputs(goo_price=0.0))
    # No PPA, no tariffs, no GoO → net = spot_revenue only
    for p in range(24):
        assert out.net_revenue[p] == pytest.approx(out.spot_revenue[p])


def test_h_annual_net_sums_monthly():
    out = calculate(_flat_inputs(periods=24))
    assert out.annual_net_revenue[0] == pytest.approx(sum(out.net_revenue[:12]))
    assert out.annual_net_revenue[1] == pytest.approx(sum(out.net_revenue[12:]))


def test_h_lifetime_total():
    out = calculate(_flat_inputs())
    assert out.total_net_revenue == pytest.approx(sum(out.net_revenue))


# ---------------------------------------------------------------------------
# Integration: Viuf proxy
# ---------------------------------------------------------------------------

def test_viuf_proxy_pv_revenue():
    """
    Proxy for Project Viuf PV (258 MWp, 1,050 MWh/MWp/year, 475 months).
    PPA: 125 GWh/year baseload @ 400 DKK/MWh, 10-year (periods 0–119).
    Expected: lifetime net revenue in DKKk plausible vs spec target ~7.1m DKKk total (PV+BESS).
    """
    periods = 475
    monthly_production = 258.0 * 1_050.0 / 12.0  # MWh/month ≈ 22,575 MWh

    ppa_vol_per_month = 125_000.0 / 12.0  # MWh/month ≈ 10,417 MWh
    ppa_vols = [ppa_vol_per_month if p < 120 else 0.0 for p in range(periods)]

    slot0 = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=400.0,
        contracted_volume=ppa_vols,
        capture_comp_breakeven_DKK_per_MWh=350.0,
        capture_comp_settlement_month=6,
    )

    out = calculate(Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        inflation_rate=0.025,
        net_production_MWh=[monthly_production] * periods,
        wholesale_price_DKK_per_MWh=[400.0] * periods,
        capture_price_DKK_per_MWh=[380.0] * periods,
        goo_price_DKK_per_MWh=[15.0] * periods,
        ppa_slots=[slot0] + [PPASlot()] * 4,
        tso_tariff=VariableTariff(rate_DKK_per_MWh=2.5, inflation_start_year=2026),
        dso_tariff=VariableTariff(rate_DKK_per_MWh=1.0, inflation_start_year=2026),
        nordpool_tariff=VariableTariff(rate_DKK_per_MWh=0.3, inflation_start_year=2026),
        imbalance_tariff=VariableTariff(rate_DKK_per_MWh=1.0, inflation_start_year=2026),
        ppa_management_rate_DKK_per_MWh=0.5,
    ))

    assert out.total_net_revenue > 1_000_000.0   # DKKk — plausible floor
    assert out.total_net_revenue < 20_000_000.0  # DKKk — plausible ceiling
    assert len(out.net_revenue) == 475
    # PPA compensation should appear in some months (capture 380 < breakeven 350? No, 380>350)
    # → no compensation triggered — verify
    assert all(v == pytest.approx(0.0) for v in out.ppa[0].capture_compensation)


def test_viuf_ppa_capture_comp_triggered():
    """Separate test: confirm compensation fires when capture < breakeven."""
    periods = 24
    ppa_vols = [10_000.0] * periods
    slot = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=400.0,
        contracted_volume=ppa_vols,
        capture_comp_breakeven_DKK_per_MWh=400.0,
        capture_comp_settlement_month=3,
    )
    out = calculate(_flat_inputs(
        periods=periods, capture=300.0, wholesale=300.0,
        inflation_rate=0.0, ppa_slots=[slot] + [PPASlot()] * 4,
    ))
    # Annual compensation = (400-300) × 120,000 MWh/year / 1000 = 12,000 DKKk
    # Settled in March of following year = period 14
    assert out.ppa[0].capture_compensation[14] == pytest.approx(12_000.0)


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "start_factor": "B5", "inf_rate": "B6", "year": "K3", "inf_start": "B7",
        "wholesale_raw": "K50", "capture_raw": "K51", "price_factor": "K$5",
        "net_prod": "K20", "capture_inf": "K52", "ppa_vol": "K60",
        "ppa_price": "G60", "wholesale_inf": "K$50", "ppa_fixed": "K61",
        "ppa_settle": "K62", "total_ppa_vol": "K65",
        "goo_price": "K70", "goo_vol": "K69", "tariff_rate": "G75",
        "ppa_mgmt_rate": "G66", "spot": "K$20", "ppa_rev": "K$65",
        "goo_rev": "K$69", "gross": "K80", "prod_costs": "K85", "balancing": "K90",
    }
    formulas = get_excel_formulas(refs)
    for key in (
        "price_indexation_factor", "wholesale_inflated", "capture_inflated",
        "spot_revenue", "ppa_fixed_payment", "ppa_settlement", "ppa_net_settlement",
        "goo_volume", "goo_revenue", "variable_tariff_cost",
        "ppa_management_cost", "gross_revenue", "net_revenue",
    ):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# External mode
# ---------------------------------------------------------------------------

def test_external_mode_returns_external_values():
    """External mode passes through external curves as net revenue."""
    n = 12
    ppa = [100.0] * n
    grid = [200.0] * n
    goo = [50.0] * n
    prod = [5_000.0] * n
    inp = Inputs(
        periods=n,
        start_year=2025,
        start_month=1,
        inflation_rate=0.0,
        net_production_MWh=[0.0] * n,
        wholesale_price_DKK_per_MWh=[0.0] * n,
        capture_price_DKK_per_MWh=[0.0] * n,
        goo_price_DKK_per_MWh=[0.0] * n,
        external_mode=True,
        external_pv_to_ppa_DKKk=ppa,
        external_pv_to_grid_DKKk=grid,
        external_goo_DKKk=goo,
        external_production_MWh=prod,
    )
    out = calculate(inp)
    assert isinstance(out, Outputs)
    assert len(out.net_revenue) == n
    for p in range(n):
        assert out.net_revenue[p] == pytest.approx(350.0)  # 100+200+50
        assert out.gross_revenue[p] == pytest.approx(350.0)
        assert out.net_production_MWh[p] == pytest.approx(5_000.0)
        assert out.total_costs[p] == pytest.approx(0.0)
    assert out.total_net_revenue == pytest.approx(350.0 * n)


def test_external_mode_false_unchanged():
    """When external_mode=False, external fields are ignored and normal calc runs."""
    out = calculate(_flat_inputs(production=1_000.0, capture=400.0, inflation_rate=0.0))
    expected_spot = 1_000.0 * 400.0 / 1000.0
    assert all(v == pytest.approx(expected_spot) for v in out.spot_revenue)
    # Confirm external fields default empty and don't interfere
    inp = _flat_inputs(production=1_000.0, capture=400.0, inflation_rate=0.0)
    assert inp.external_mode is False
    assert inp.external_pv_to_ppa_DKKk == []


def test_external_mode_validation():
    """External mode validates that non-empty external arrays match periods."""
    with pytest.raises(ValueError, match="external_pv_to_ppa_DKKk"):
        Inputs(
            periods=12,
            start_year=2025,
            start_month=1,
            net_production_MWh=[0.0] * 12,
            wholesale_price_DKK_per_MWh=[0.0] * 12,
            capture_price_DKK_per_MWh=[0.0] * 12,
            goo_price_DKK_per_MWh=[0.0] * 12,
            external_mode=True,
            external_pv_to_ppa_DKKk=[1.0] * 10,  # wrong length
        )
