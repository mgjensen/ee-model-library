"""Tests for PL_001 — Profit & Loss statement module."""

import pytest
from modules.statements.PL_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _year_groups,
    _annual,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    gross_revenue=None,
    total_opex=None,
    depreciation=None,
    interest_expense=None,
    tax_charge=None,
) -> Inputs:
    n = periods
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        gross_revenue=gross_revenue or [1_000.0] * n,
        total_opex=total_opex or [400.0] * n,
        depreciation=depreciation or [100.0] * n,
        interest_expense=interest_expense or [50.0] * n,
        tax_charge=tax_charge or [99.0] * n,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_wrong_length_gross_revenue():
    with pytest.raises(ValueError, match="gross_revenue"):
        Inputs(
            periods=12,
            start_year=2025,
            start_month=1,
            gross_revenue=[1_000.0] * 6,  # wrong
            total_opex=[400.0] * 12,
            depreciation=[100.0] * 12,
            interest_expense=[50.0] * 12,
            tax_charge=[99.0] * 12,
        )


def test_wrong_length_tax_charge():
    with pytest.raises(ValueError, match="tax_charge"):
        Inputs(
            periods=12,
            start_year=2025,
            start_month=1,
            gross_revenue=[1_000.0] * 12,
            total_opex=[400.0] * 12,
            depreciation=[100.0] * 12,
            interest_expense=[50.0] * 12,
            tax_charge=[99.0] * 6,  # wrong
        )


# ---------------------------------------------------------------------------
# Unit: line item calculations
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_ebitda_is_revenue_minus_opex():
    out = calculate(_base_inputs(
        gross_revenue=[1_000.0] * 24,
        total_opex=[400.0] * 24,
    ))
    assert all(v == pytest.approx(600.0) for v in out.ebitda)


def test_ebit_is_ebitda_minus_dep():
    out = calculate(_base_inputs(
        gross_revenue=[1_000.0] * 24,
        total_opex=[400.0] * 24,
        depreciation=[100.0] * 24,
    ))
    assert all(v == pytest.approx(500.0) for v in out.ebit)


def test_ebt_is_ebit_minus_interest():
    out = calculate(_base_inputs(
        gross_revenue=[1_000.0] * 24,
        total_opex=[400.0] * 24,
        depreciation=[100.0] * 24,
        interest_expense=[50.0] * 24,
    ))
    assert all(v == pytest.approx(450.0) for v in out.ebt)


def test_net_income_is_ebt_minus_tax():
    out = calculate(_base_inputs(
        gross_revenue=[1_000.0] * 24,
        total_opex=[400.0] * 24,
        depreciation=[100.0] * 24,
        interest_expense=[50.0] * 24,
        tax_charge=[99.0] * 24,
    ))
    assert all(v == pytest.approx(351.0) for v in out.net_income)


def test_net_income_identity():
    """net_income = gross_revenue - opex - dep - interest - tax."""
    out = calculate(_base_inputs())
    for p in range(24):
        expected = (
            out.gross_revenue[p] - out.total_opex[p]
            - out.depreciation[p] - out.interest_expense[p]
            - out.tax_charge[p]
        )
        assert out.net_income[p] == pytest.approx(expected)


def test_zero_tax_net_equals_ebt():
    out = calculate(_base_inputs(tax_charge=[0.0] * 24))
    for p in range(24):
        assert out.net_income[p] == pytest.approx(out.ebt[p])


def test_negative_net_income_when_costs_exceed_revenue():
    out = calculate(_base_inputs(
        gross_revenue=[100.0] * 24,
        total_opex=[200.0] * 24,
        depreciation=[0.0] * 24,
        interest_expense=[0.0] * 24,
        tax_charge=[0.0] * 24,
    ))
    assert all(v == pytest.approx(-100.0) for v in out.net_income)


# ---------------------------------------------------------------------------
# Unit: passthrough of input series to output
# ---------------------------------------------------------------------------

def test_gross_revenue_passthrough():
    rev = [float(p * 100) for p in range(24)]
    out = calculate(_base_inputs(gross_revenue=rev))
    assert out.gross_revenue == pytest.approx(rev)


def test_depreciation_passthrough():
    dep = [50.0 + p for p in range(24)]
    out = calculate(_base_inputs(depreciation=dep))
    assert out.depreciation == pytest.approx(dep)


# ---------------------------------------------------------------------------
# Unit: annual aggregation
# ---------------------------------------------------------------------------

def test_annual_ebitda_sums_monthly():
    out = calculate(_base_inputs(periods=24, start_year=2025, start_month=1))
    assert out.annual_ebitda[0] == pytest.approx(sum(out.ebitda[:12]))
    assert out.annual_ebitda[1] == pytest.approx(sum(out.ebitda[12:]))


def test_annual_count_two_years():
    out = calculate(_base_inputs(periods=24))
    assert len(out.annual_ebitda) == 2
    assert len(out.annual_net_income) == 2


def test_annual_count_partial_year():
    """Start in July → first year has 6 periods, second has 12, third has 6."""
    out = calculate(_base_inputs(periods=24, start_year=2025, start_month=7))
    assert len(out.annual_gross_revenue) == 3


def test_annual_net_income_sums_monthly():
    out = calculate(_base_inputs(periods=36, start_year=2025, start_month=1))
    for i, plist in enumerate(_year_groups(36, 2025, 1).values()):
        expected = sum(out.net_income[p] for p in plist)
        assert out.annual_net_income[i] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Unit: lifetime totals
# ---------------------------------------------------------------------------

def test_lifetime_gross_revenue():
    out = calculate(_base_inputs())
    assert out.lifetime_gross_revenue == pytest.approx(sum(out.gross_revenue))


def test_lifetime_opex():
    out = calculate(_base_inputs())
    assert out.lifetime_total_opex == pytest.approx(sum(out.total_opex))


def test_lifetime_ebitda():
    out = calculate(_base_inputs())
    assert out.lifetime_ebitda == pytest.approx(sum(out.ebitda))


def test_lifetime_net_income():
    out = calculate(_base_inputs())
    assert out.lifetime_net_income == pytest.approx(sum(out.net_income))


# ---------------------------------------------------------------------------
# Integration: Viuf-scale proxy
# ---------------------------------------------------------------------------

def test_viuf_pl_proxy():
    """475-period project with realistic magnitudes."""
    periods = 475
    rev = [8_000.0] * periods      # ~96 MKK/yr gross revenue
    opex = [2_500.0] * periods     # ~30 MKK/yr OPEX
    dep = [1_200.0] * periods      # ~14.4 MKK/yr depreciation
    interest = [800.0] * periods   # ~9.6 MKK/yr interest
    tax = [500.0] * periods        # ~6 MKK/yr tax

    out = calculate(Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        gross_revenue=rev,
        total_opex=opex,
        depreciation=dep,
        interest_expense=interest,
        tax_charge=tax,
    ))

    assert out.lifetime_gross_revenue == pytest.approx(8_000.0 * 475)
    assert out.lifetime_ebitda == pytest.approx((8_000.0 - 2_500.0) * 475)
    assert out.lifetime_net_income > 0
    assert len(out.annual_net_income) > 0


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "gross_revenue": "K10", "total_opex": "K11", "ebitda": "K12",
        "depreciation": "K13", "ebit": "K14", "interest_expense": "K15",
        "ebt": "K16", "tax_charge": "K17",
    }
    formulas = get_excel_formulas(refs)
    for key in ("ebitda", "ebit", "ebt", "net_income"):
        assert key in formulas
        assert formulas[key].startswith("=")
