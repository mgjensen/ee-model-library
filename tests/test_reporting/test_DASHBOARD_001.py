"""Tests for DASHBOARD_001 — KPI dashboard module."""

import pytest
from modules.reporting.DASHBOARD_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**overrides) -> Inputs:
    """Standard dashboard inputs for a 100 MW PV + 50 MW BESS project."""
    defaults = dict(
        project_irr=0.085,
        equity_irr=0.12,
        project_npv=500_000.0,
        equity_npv=200_000.0,
        res_capacity_MW=100.0,
        bess_capacity_MW=50.0,
        bess_capacity_MWh=200.0,
        production_year1_MWh=175_000.0,
        total_capex_DKKk=400_000.0,
        remaining_capex_after_closing_DKKk=50_000.0,
        total_debt_DKKk=280_000.0,
        gearing_pct=0.70,
        sizing_constraint="leverage_cap",
        avg_dscr=1.35,
        min_dscr=1.20,
        avg_llcr=1.50,
        min_llcr=1.30,
        cash_at_closing_DKKk=10_000.0,
        nwc_at_closing_DKKk=5_000.0,
        total_revenue_lifetime=800_000.0,
        total_opex_lifetime=200_000.0,
        total_ebitda_lifetime=600_000.0,
        total_tax_lifetime=80_000.0,
        investor_ownership_pct=0.50,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ev_per_MW():
    out = calculate(_make_inputs())
    # EV = 500_000, total capacity = 150 MW → 500_000 / 150 ≈ 3333.33
    assert abs(out.ev_per_MW - 500_000.0 / 150.0) < 0.01


def test_purchase_price_bridge():
    out = calculate(_make_inputs())
    # pp100 = EV - debt - cash - nwc = 500k - 280k - 10k - 5k = 205k
    assert abs(out.purchase_price_100pct - 205_000.0) < 0.01


def test_equity_ticket():
    out = calculate(_make_inputs())
    # pp_inv = 205_000 * 0.50 = 102_500
    # ticket = 102_500 + 50_000 * 0.50 = 127_500
    assert abs(out.total_equity_ticket_investor - 127_500.0) < 0.01


def test_gearing_passthrough():
    out = calculate(_make_inputs(gearing_pct=0.65))
    assert out.gearing_actual == 0.65


def test_profit_metrics():
    out = calculate(_make_inputs())
    # profit = EV - capex = 500k - 400k = 100k
    assert abs(out.profit_from_sale_DKKk - 100_000.0) < 0.01


def test_zero_capacity_handling():
    """No divide-by-zero when both capacities are zero."""
    out = calculate(_make_inputs(
        res_capacity_MW=0.0,
        bess_capacity_MW=0.0,
        production_year1_MWh=0.0,
    ))
    assert out.ev_per_MW == 0.0
    assert out.ev_per_MWh == 0.0
    assert out.capex_per_MW == 0.0


def test_output_fields_populated():
    out = calculate(_make_inputs())
    assert isinstance(out, Outputs)
    # Every field should be set (no None)
    for field_name in Outputs.model_fields:
        assert getattr(out, field_name) is not None


def test_no_debt_all_equity_purchase():
    """With zero debt, purchase price = EV - cash - nwc."""
    out = calculate(_make_inputs(total_debt_DKKk=0.0))
    expected_pp = 500_000.0 - 10_000.0 - 5_000.0
    assert abs(out.purchase_price_100pct - expected_pp) < 0.01
    assert out.total_debt_supported == 0.0


def test_capex_per_MW():
    out = calculate(_make_inputs())
    # 400_000 / 150 ≈ 2666.67
    assert abs(out.capex_per_MW - 400_000.0 / 150.0) < 0.01


def test_sizing_constraint_string():
    out = calculate(_make_inputs(sizing_constraint="dscr"))
    assert out.sizing_constraint == "dscr"


def test_dscr_passthrough():
    out = calculate(_make_inputs(avg_dscr=1.40, min_dscr=1.15))
    assert out.avg_dscr == 1.40
    assert out.min_dscr == 1.15


def test_llcr_passthrough():
    out = calculate(_make_inputs(avg_llcr=1.55, min_llcr=1.25))
    assert out.avg_llcr == 1.55
    assert out.min_llcr == 1.25


def test_profit_over_capex_pct():
    out = calculate(_make_inputs())
    # profit = 100k, capex = 400k → 0.25
    assert abs(out.profit_over_capex_pct - 0.25) < 1e-6


def test_ebitda_margin():
    out = calculate(_make_inputs())
    # 600k / 800k = 0.75
    assert abs(out.ebitda_margin - 0.75) < 1e-6


def test_ev_equals_project_npv():
    out = calculate(_make_inputs(project_npv=123_456.0))
    assert out.ev_DKKk == 123_456.0
