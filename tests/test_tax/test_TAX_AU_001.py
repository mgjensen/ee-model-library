"""Tests for TAX_AU_001 — Australian corporate tax module."""

import pytest
from modules.tax.TAX_AU_001 import Inputs, Outputs, calculate, get_excel_formulas


def _make(
    periods=12, start_year=2028, start_month=1,
    tax_rate=0.30, loss_cf_active=True,
    ebitda_annual=120_000.0, interest_annual=12_000.0,
    capex_year1=0.0, lifetimes=None,
):
    n = periods
    monthly_ebitda = [ebitda_annual / 12] * n
    monthly_interest = [interest_annual / 12] * n
    capex = [[0.0] * n for _ in range(4)]
    if capex_year1 > 0:
        capex[0][0] = capex_year1
    return Inputs(
        periods=n, start_year=start_year, start_month=start_month,
        tax_rate=tax_rate, loss_cf_active=loss_cf_active,
        ebitda=monthly_ebitda, interest_expense=monthly_interest,
        capex_by_bucket=capex,
        opening_balances=[0.0] * 4,
        depreciation_lifetimes=lifetimes or [30, 30, 30, 30],
    )


def test_zero_ebitda_zero_tax():
    out = calculate(_make(ebitda_annual=0.0, interest_annual=0.0))
    assert out.total_tax_paid == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in out.tax_charge_accrued)


def test_positive_ebitda_30pct_tax():
    out = calculate(_make(ebitda_annual=100_000.0, interest_annual=0.0))
    assert sum(out.tax_charge_accrued) == pytest.approx(100_000.0 * 0.30, rel=1e-4)


def test_loss_carryforward():
    n = 24
    ebitda = [-50_000.0 / 12] * 12 + [80_000.0 / 12] * 12
    interest = [0.0] * n
    inp = Inputs(
        periods=n, start_year=2028, start_month=1, tax_rate=0.30,
        loss_cf_active=True,
        ebitda=ebitda, interest_expense=interest,
        capex_by_bucket=[[0.0] * n for _ in range(4)],
        opening_balances=[0.0] * 4,
        depreciation_lifetimes=[30, 30, 30, 30],
    )
    out = calculate(inp)
    # Year 1: loss 50k -> pool 50k. Year 2: gross 80k, offset 50k -> taxable 30k
    # Tax = 30k * 0.30 = 9k
    assert sum(out.tax_charge_accrued) == pytest.approx(9_000.0, rel=1e-4)


def test_loss_cf_inactive():
    n = 24
    ebitda = [-50_000.0 / 12] * 12 + [80_000.0 / 12] * 12
    interest = [0.0] * n
    inp = Inputs(
        periods=n, start_year=2028, start_month=1, tax_rate=0.30,
        loss_cf_active=False,
        ebitda=ebitda, interest_expense=interest,
        capex_by_bucket=[[0.0] * n for _ in range(4)],
        opening_balances=[0.0] * 4,
        depreciation_lifetimes=[30, 30, 30, 30],
    )
    out = calculate(inp)
    # No loss offset -> year 2 taxable = 80k, tax = 24k
    assert sum(out.tax_charge_accrued) == pytest.approx(80_000.0 * 0.30, rel=1e-4)


def test_straight_line_depreciation():
    out = calculate(_make(ebitda_annual=500_000.0, capex_year1=300_000.0, lifetimes=[30, 30, 30, 30]))
    # Year 1: 300k / 30 = 10k annual dep -> 10k/12 per month
    assert out.tax_depreciation[0] == pytest.approx(10_000.0 / 12, rel=1e-3)


def test_depreciation_reduces_taxable_income():
    out_no_dep = calculate(_make(ebitda_annual=100_000.0, interest_annual=10_000.0, capex_year1=0.0))
    out_dep = calculate(_make(ebitda_annual=100_000.0, interest_annual=10_000.0, capex_year1=300_000.0))
    # More depreciation -> lower taxable -> lower tax
    assert sum(out_dep.tax_charge_accrued) < sum(out_no_dep.tax_charge_accrued)


def test_interest_fully_deductible():
    out = calculate(_make(ebitda_annual=100_000.0, interest_annual=40_000.0))
    # Taxable = 100k - 40k = 60k. Tax = 60k * 0.30 = 18k
    assert sum(out.tax_charge_accrued) == pytest.approx(18_000.0, rel=1e-4)
    assert all(v == pytest.approx(0.0) for v in out.non_deductible_interest)


def test_tax_payment_timing():
    out = calculate(_make(periods=36, ebitda_annual=100_000.0, interest_annual=0.0))
    for p, val in enumerate(out.tax_paid):
        if abs(val) > 1e-9:
            month = ((1 - 1 + p) % 12) + 1
            assert month == 6, f"Payment in month {month} (period {p}), expected June"


def test_bucket_padding_4_to_7():
    inp = _make(capex_year1=100_000.0)
    assert len(inp.capex_by_bucket) == 7
    assert len(inp.opening_balances) == 7
    assert len(inp.depreciation_lifetimes) == 7


def test_mulwala_calibration_values():
    """Mulwala reference: EBITDA 12,089,832, dep 3,587,733, interest 4,310,961."""
    n = 396  # 33 years
    ebitda_yr1 = 12_089_832.0
    dep_yr1 = 3_587_733.0
    interest_yr1 = 4_310_961.0
    asset = dep_yr1 * 30  # 107.6M

    ebitda = [ebitda_yr1 / 12] * n
    interest = [interest_yr1 / 12] * n
    capex = [[0.0] * n for _ in range(4)]
    inp = Inputs(
        periods=n, start_year=2025, start_month=1, tax_rate=0.30,
        ebitda=ebitda, interest_expense=interest,
        capex_by_bucket=capex,
        opening_balances=[asset, 0.0, 0.0, 0.0],
        depreciation_lifetimes=[30, 30, 30, 30],
    )
    out = calculate(inp)
    # Year 1 taxable: 12,089,832 - 3,587,733 - 4,310,961 = 4,191,138
    # Tax: 4,191,138 * 0.30 = 1,257,341
    yr1_charge = sum(out.tax_charge_accrued[:12])
    assert yr1_charge == pytest.approx(1_257_341.0, abs=5_000)


def test_excel_formulas():
    refs = {
        "opening_bal": "C10", "capex": "C11", "lifetime": "B5",
        "ebitda": "H20", "annual_dep": "H23", "interest": "H21",
        "taxable_income": "H24", "tax_rate": "B8",
    }
    formulas = get_excel_formulas(refs)
    for key in ("straight_line_dep", "closing_balance", "taxable_income_gross", "tax_charge"):
        assert key in formulas
        assert formulas[key].startswith("=")
