"""Tests for SOURCES_USES_001 — construction sources & uses waterfall."""

import math
import pytest
from modules.statements.SOURCES_USES_001 import Inputs, Outputs, calculate, get_excel_formulas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**kwargs):
    defaults = dict(
        periods=192,
        start_year=2025,
        start_month=1,
        construction_start_period=0,
        cod_period=12,
        total_capex_DKKk=200_000.0,
        vat_on_capex_DKKk=50_000.0,
        arrangement_fees_total_DKKk=3_000.0,
        idc_construction_DKKk=2_000.0,
        idc_senior_DKKk=1_500.0,
        commitment_fees_DKKk=500.0,
        vat_facility_costs_DKKk=200.0,
        dsra_initial_DKKk=10_000.0,
        minimum_cash_DKKk=1_000.0,
        pre_completion_opex_net_DKKk=800.0,
        senior_debt_drawn_DKKk=160_000.0,
        shl_drawn_DKKk=20_000.0,
        vat_facility_drawn_DKKk=50_000.0,
        pre_completion_revenue_DKKk=500.0,
        vat_reimbursements_DKKk=0.0,
    )
    defaults.update(kwargs)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sources_equals_uses():
    """Total sources must equal total uses."""
    out = calculate(_make_inputs())
    assert abs(out.total_sources - out.total_uses) < 1e-6


def test_equity_is_residual():
    """Equity = total uses minus non-equity sources."""
    inp = _make_inputs()
    out = calculate(inp)
    non_equity = (
        inp.senior_debt_drawn_DKKk
        + inp.shl_drawn_DKKk
        + inp.vat_facility_drawn_DKKk
        + inp.pre_completion_revenue_DKKk
        + inp.vat_reimbursements_DKKk
    )
    expected_equity = out.total_uses - non_equity
    assert abs(out.equity_required_DKKk - expected_equity) < 1e-6


def test_gearing_calculation():
    """Gearing = senior debt / total uses."""
    inp = _make_inputs(senior_debt_drawn_DKKk=160_000.0)
    out = calculate(inp)
    expected = 160_000.0 / out.total_uses
    assert abs(out.gearing_pct - expected) < 1e-9


def test_zero_debt_all_equity():
    """With zero debt sources, equity covers everything."""
    out = calculate(_make_inputs(
        senior_debt_drawn_DKKk=0.0,
        shl_drawn_DKKk=0.0,
        vat_facility_drawn_DKKk=0.0,
        pre_completion_revenue_DKKk=0.0,
        vat_reimbursements_DKKk=0.0,
    ))
    assert out.equity_required_DKKk == out.total_uses
    assert abs(out.equity_pct - 1.0) < 1e-9
    assert abs(out.gearing_pct) < 1e-9


def test_high_debt_low_equity():
    """When debt covers most of uses, equity is small."""
    total_uses_expected = 269_000.0  # sum of all default uses
    out = calculate(_make_inputs(senior_debt_drawn_DKKk=250_000.0))
    assert out.equity_required_DKKk < 0.05 * total_uses_expected


def test_vat_in_uses():
    """VAT on capex appears in total uses."""
    out = calculate(_make_inputs(vat_on_capex_DKKk=50_000.0))
    assert out.uses_vat == 50_000.0


def test_dsra_in_uses():
    """DSRA initial deposit appears in total uses."""
    out = calculate(_make_inputs(dsra_initial_DKKk=10_000.0))
    assert out.uses_dsra == 10_000.0


def test_idc_in_uses():
    """IDC (construction + senior) appears in total uses."""
    out = calculate(_make_inputs(
        idc_construction_DKKk=2_000.0,
        idc_senior_DKKk=1_500.0,
    ))
    assert out.uses_idc_construction == 2_000.0
    assert out.uses_idc_senior == 1_500.0


def test_pre_completion_revenue_reduces_equity():
    """Pre-completion revenue is a source that reduces equity requirement."""
    out_without = calculate(_make_inputs(pre_completion_revenue_DKKk=0.0))
    out_with = calculate(_make_inputs(pre_completion_revenue_DKKk=5_000.0))
    assert out_with.equity_required_DKKk < out_without.equity_required_DKKk
    diff = out_without.equity_required_DKKk - out_with.equity_required_DKKk
    assert abs(diff - 5_000.0) < 1e-6


def test_balance_within_tolerance():
    """Balance check should be near zero and balance_ok should be True."""
    out = calculate(_make_inputs())
    assert out.balance_ok is True
    assert abs(out.balance_check) < 1e-6


def test_no_capex_zero_everything():
    """With all inputs zero, equity is zero and balance is satisfied."""
    out = calculate(_make_inputs(
        total_capex_DKKk=0.0,
        vat_on_capex_DKKk=0.0,
        arrangement_fees_total_DKKk=0.0,
        idc_construction_DKKk=0.0,
        idc_senior_DKKk=0.0,
        commitment_fees_DKKk=0.0,
        vat_facility_costs_DKKk=0.0,
        dsra_initial_DKKk=0.0,
        minimum_cash_DKKk=0.0,
        pre_completion_opex_net_DKKk=0.0,
        senior_debt_drawn_DKKk=0.0,
        shl_drawn_DKKk=0.0,
        vat_facility_drawn_DKKk=0.0,
        pre_completion_revenue_DKKk=0.0,
        vat_reimbursements_DKKk=0.0,
    ))
    assert out.total_uses == 0.0
    assert out.equity_required_DKKk == 0.0
    assert out.balance_ok is True


def test_equity_plus_gearing_equals_100():
    """Equity pct + gearing pct + other sources pct = 100%."""
    out = calculate(_make_inputs())
    # All source percentages relative to total uses should sum to 1.0
    other_pct = (
        out.sources_shl
        + out.sources_vat_facility
        + out.sources_pre_completion_revenue
        + out.sources_vat_reimbursements
    ) / out.total_uses
    total_pct = out.gearing_pct + out.equity_pct + other_pct
    assert abs(total_pct - 1.0) < 1e-9


def test_arrangement_fees_in_uses():
    """Arrangement fees appear in total uses."""
    out = calculate(_make_inputs(arrangement_fees_total_DKKk=5_000.0))
    assert out.uses_arrangement_fees == 5_000.0
    assert out.total_uses >= 5_000.0


def test_minimum_cash_in_uses():
    """Minimum cash requirement appears in total uses."""
    out = calculate(_make_inputs(minimum_cash_DKKk=2_000.0))
    assert out.uses_minimum_cash == 2_000.0


def test_equity_monthly_length():
    """equity_monthly has length == periods."""
    for n in [60, 120, 192, 360]:
        out = calculate(_make_inputs(periods=n))
        assert len(out.equity_monthly) == n


def test_equity_monthly_sums_to_equity_required():
    """Sum of equity_monthly should equal equity_required_DKKk."""
    out = calculate(_make_inputs())
    assert abs(sum(out.equity_monthly) - out.equity_required_DKKk) < 1e-6


def test_equity_monthly_only_during_construction():
    """Equity disbursements only occur between construction_start and cod."""
    out = calculate(_make_inputs(
        construction_start_period=3,
        cod_period=12,
    ))
    # Before construction start: zero
    for p in range(3):
        assert out.equity_monthly[p] == 0.0
    # After COD: zero
    for p in range(12, len(out.equity_monthly)):
        assert out.equity_monthly[p] == 0.0


def test_get_excel_formulas():
    """get_excel_formulas returns a dict with expected keys."""
    formulas = get_excel_formulas({})
    assert "total_uses" in formulas
    assert "total_sources" in formulas
    assert "equity_required_DKKk" in formulas
