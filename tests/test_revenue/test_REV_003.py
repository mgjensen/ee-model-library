"""Tests for REV_003 — Wind revenue module."""

import pytest
from modules.revenue.REV_003 import (
    Inputs,
    Outputs,
    PPASlot,
    VariableTariff,
    calculate,
    get_excel_formulas,
    _year_groups,
    _indexation_factor,
    _variable_cost_series,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=24,
    start_year=2026,
    start_month=1,
    production=None,
    wholesale=None,
    capture=None,
    goo=None,
) -> Inputs:
    n = periods
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        net_production_MWh=production or [1_000.0] * n,
        wholesale_price_DKK_per_MWh=wholesale or [400.0] * n,
        capture_price_DKK_per_MWh=capture or [390.0] * n,  # wind capture ~97% of wholesale
        goo_price_DKK_per_MWh=goo or [5.0] * n,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_wrong_length_production():
    with pytest.raises(ValueError, match="net_production_MWh"):
        Inputs(
            periods=12,
            start_year=2026, start_month=1,
            net_production_MWh=[1000.0] * 6,
            wholesale_price_DKK_per_MWh=[400.0] * 12,
            capture_price_DKK_per_MWh=[390.0] * 12,
            goo_price_DKK_per_MWh=[5.0] * 12,
        )


def test_wrong_ppa_slots_count():
    with pytest.raises(ValueError, match="ppa_slots"):
        Inputs(
            periods=12,
            start_year=2026, start_month=1,
            net_production_MWh=[1000.0] * 12,
            wholesale_price_DKK_per_MWh=[400.0] * 12,
            capture_price_DKK_per_MWh=[390.0] * 12,
            goo_price_DKK_per_MWh=[5.0] * 12,
            ppa_slots=[PPASlot()] * 3,  # wrong count
        )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs_instance():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_output_array_lengths():
    n = 36
    out = calculate(_base_inputs(periods=n))
    for field in ("net_production_MWh", "spot_revenue", "gross_revenue",
                  "total_costs", "net_revenue"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Spot revenue
# ---------------------------------------------------------------------------

def test_spot_revenue_no_ppa():
    """spot_revenue = net_prod × capture_price / 1000 (no PPA, no inflation)."""
    n = 12
    prod = [1_000.0] * n
    cap  = [400.0] * n
    out = calculate(_base_inputs(n, production=prod, capture=cap).model_copy(update={"inflation_rate": 0.0}))
    for p in range(n):
        assert out.spot_revenue[p] == pytest.approx(prod[p] * cap[p] / 1000.0)


def test_gross_revenue_equals_spot_plus_goo_when_no_ppa():
    n = 12
    out = calculate(_base_inputs(n))
    for p in range(n):
        expected = out.spot_revenue[p] + out.goo_revenue[p]
        assert out.gross_revenue[p] == pytest.approx(expected)


def test_net_revenue_equals_gross_minus_costs():
    n = 12
    out = calculate(_base_inputs(n))
    for p in range(n):
        assert out.net_revenue[p] == pytest.approx(
            out.gross_revenue[p] - out.total_costs[p]
        )


# ---------------------------------------------------------------------------
# GoO
# ---------------------------------------------------------------------------

def test_goo_volume_equals_production_when_no_ppa():
    n = 12
    out = calculate(_base_inputs(n))
    for p in range(n):
        assert out.goo_volume[p] == pytest.approx(out.net_production_MWh[p])


def test_goo_revenue_computed_correctly():
    n = 12
    goo_price = [5.0] * n
    out = calculate(_base_inputs(n, goo=goo_price))
    for p in range(n):
        assert out.goo_revenue[p] == pytest.approx(
            out.goo_volume[p] * goo_price[p] / 1000.0
        )


# ---------------------------------------------------------------------------
# Production costs
# ---------------------------------------------------------------------------

def test_zero_tariffs_give_zero_costs():
    n = 12
    out = calculate(_base_inputs(n))
    for p in range(n):
        assert out.total_production_costs[p] == pytest.approx(0.0)
        assert out.balancing_cost[p] == pytest.approx(0.0)


def test_tso_tariff_applied():
    n = 12
    inp = _base_inputs(n)
    inp = inp.model_copy(update={
        "tso_tariff": VariableTariff(rate_DKK_per_MWh=2.0, inflation_start_year=2025)
    })
    out = calculate(inp)
    for p in range(n):
        assert out.tso_cost[p] > 0.0


def test_balancing_cost_applied():
    n = 12
    inp = _base_inputs(n)
    inp = inp.model_copy(update={
        "imbalance_tariff": VariableTariff(rate_DKK_per_MWh=3.0, inflation_start_year=2025)
    })
    out = calculate(inp)
    for p in range(n):
        assert out.balancing_cost[p] > 0.0


# ---------------------------------------------------------------------------
# PPA slot
# ---------------------------------------------------------------------------

def test_active_ppa_reduces_goo():
    n = 12
    ppa_vol = 200.0
    slots = [PPASlot()] * 5
    slots[0] = PPASlot(
        active=True,
        ppa_price_DKK_per_MWh=450.0,
        contracted_volume=[ppa_vol] * n,
    )
    inp = _base_inputs(n)
    inp = inp.model_copy(update={"ppa_slots": slots})
    out = calculate(inp)
    for p in range(n):
        assert out.goo_volume[p] == pytest.approx(
            out.net_production_MWh[p] - ppa_vol
        )


# ---------------------------------------------------------------------------
# Price indexation
# ---------------------------------------------------------------------------

def test_price_indexation_factor_at_start():
    """First year period 0 → factor should equal indexation_start_factor."""
    out = calculate(_base_inputs(periods=12, start_year=2025))
    assert out.price_indexation_factor[0] == pytest.approx(1.0)


def test_price_indexation_increases_over_time():
    out = calculate(_base_inputs(periods=24, start_year=2025))
    # Year 2 factor > year 1 factor
    assert out.price_indexation_factor[12] > out.price_indexation_factor[0]


# ---------------------------------------------------------------------------
# Annual aggregation
# ---------------------------------------------------------------------------

def test_annual_net_revenue_length():
    n = 24
    out = calculate(_base_inputs(periods=n, start_year=2026, start_month=1))
    assert len(out.annual_net_revenue) == 2   # 24 months = 2 years


def test_total_net_revenue_equals_sum():
    out = calculate(_base_inputs(periods=24))
    assert out.total_net_revenue == pytest.approx(sum(out.net_revenue))


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "start_factor": "B5", "inf_rate": "B6", "year": "C3", "inf_start": "B7",
        "wholesale_raw": "C10", "capture_raw": "C11", "price_factor": "C8",
        "net_prod": "C6", "capture_inf": "C11",
        "ppa_vol": "C15", "ppa_price": "B15", "wholesale_inf": "C10",
        "ppa_fixed": "C16", "ppa_settle": "C17",
        "total_ppa_vol": "C20", "goo_price": "B22",
        "goo_vol": "C21",
        "tariff_rate": "B25", "spot": "C14", "ppa_rev": "C19", "goo_rev": "C22",
        "prod_costs": "C26", "balancing": "C27", "gross": "C28",
        "ppa_mgmt_rate": "B30",
    }
    formulas = get_excel_formulas(refs)
    for key in ("spot_revenue", "gross_revenue", "net_revenue"):
        assert key in formulas
        assert formulas[key].startswith("=")
