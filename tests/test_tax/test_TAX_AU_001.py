"""Tests for TAX_AU_001 — Australian corporate tax module (declining balance)."""

import pytest
from modules.tax.TAX_AU_001 import Inputs, Outputs, calculate, get_excel_formulas


def _make(
    periods=12, start_year=2028, start_month=1,
    tax_rate=0.30, loss_cfwd_active=True,
    ebitda_annual=120_000.0, interest_annual=12_000.0,
    capex_year1=0.0, dep_rates=None, n_buckets=4,
    opening_balances=None,
):
    n = periods
    monthly_ebitda = [ebitda_annual / 12] * n
    monthly_interest = [interest_annual / 12] * n
    capex = [[0.0] * n for _ in range(n_buckets)]
    if capex_year1 > 0:
        capex[0][0] = capex_year1
    return Inputs(
        periods=n, start_year=start_year, start_month=start_month,
        tax_rate=tax_rate, loss_cfwd_active=loss_cfwd_active,
        ebitda=monthly_ebitda, total_interest=monthly_interest,
        capex_by_bucket=capex,
        opening_balances=opening_balances or [0.0] * n_buckets,
        depreciation_rates=dep_rates or [0.25] * n_buckets,
    )


# ---------------------------------------------------------------------------
# Basic positive income
# ---------------------------------------------------------------------------

def test_basic_positive_income():
    out = calculate(_make(ebitda_annual=100_000.0, interest_annual=0.0))
    assert sum(out.annual_tax_charge) == pytest.approx(100_000.0 * 0.30, rel=1e-4)


# ---------------------------------------------------------------------------
# Declining balance depreciation
# ---------------------------------------------------------------------------

def test_declining_balance():
    """Multi-bucket declining balance: bucket0=300k@25%, bucket1=100k@10%."""
    n = 24
    capex = [[0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n]
    capex[0][0] = 300_000.0
    capex[1][0] = 100_000.0
    inp = Inputs(
        periods=n, start_year=2028, start_month=1,
        tax_rate=0.30, ebitda=[50_000.0 / 12] * n,
        total_interest=[0.0] * n,
        capex_by_bucket=capex,
        opening_balances=[0.0] * 4,
        depreciation_rates=[0.25, 0.10, 0.25, 0.25],
    )
    out = calculate(inp)
    # Year 1: bucket0 = 300k * 0.25 = 75k, bucket1 = 100k * 0.10 = 10k
    yr1_dep = out.tax_depreciation[0] * 12
    assert yr1_dep == pytest.approx(85_000.0, rel=1e-3)
    # Year 2: bucket0 = 225k * 0.25 = 56.25k, bucket1 = 90k * 0.10 = 9k
    yr2_dep = out.tax_depreciation[12] * 12
    assert yr2_dep == pytest.approx(65_250.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Loss carry-forward
# ---------------------------------------------------------------------------

def test_loss_carry_forward():
    n = 24
    ebitda = [-50_000.0 / 12] * 12 + [80_000.0 / 12] * 12
    interest = [0.0] * n
    inp = Inputs(
        periods=n, start_year=2028, start_month=1, tax_rate=0.30,
        loss_cfwd_active=True,
        ebitda=ebitda, total_interest=interest,
        capex_by_bucket=[[0.0] * n for _ in range(4)],
        opening_balances=[0.0] * 4,
        depreciation_rates=[0.25] * 4,
    )
    out = calculate(inp)
    # Year 1: loss 50k -> pool 50k. Year 2: gross 80k, offset 50k -> taxable 30k
    assert sum(out.annual_tax_charge) == pytest.approx(9_000.0, rel=1e-4)
    assert out.annual_loss_pool[0] == pytest.approx(50_000.0)
    assert out.annual_loss_pool[1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# PAYG timing
# ---------------------------------------------------------------------------

def test_payg_timing():
    """Verify PAYG falls in Oct, Jan, Apr, Jul."""
    out = calculate(_make(periods=60, ebitda_annual=100_000.0, interest_annual=0.0))
    payg_months = {10, 1, 4, 7}
    for p, val in enumerate(out.tax_paid):
        if abs(val) > 1e-9:
            month = ((1 - 1 + p) % 12) + 1
            assert month in payg_months, f"PAYG in month {month} (period {p}), expected Oct/Jan/Apr/Jul"


# ---------------------------------------------------------------------------
# Zero income
# ---------------------------------------------------------------------------

def test_zero_income():
    out = calculate(_make(ebitda_annual=0.0, interest_annual=0.0))
    assert out.total_lifetime_tax == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in out.corporate_tax_charge)
    assert all(v == pytest.approx(0.0) for v in out.tax_paid)


# ---------------------------------------------------------------------------
# Multi-year lifecycle
# ---------------------------------------------------------------------------

def test_multi_year_lifecycle():
    """5+ years with declining balance — verify loss pool drains."""
    n = 72  # 6 years
    ebitda = [-20_000.0 / 12] * 12 + [60_000.0 / 12] * 60
    interest = [0.0] * n
    capex = [[0.0] * n for _ in range(4)]
    capex[0][0] = 100_000.0
    inp = Inputs(
        periods=n, start_year=2028, start_month=1, tax_rate=0.30,
        loss_cfwd_active=True,
        ebitda=ebitda, total_interest=interest,
        capex_by_bucket=capex,
        opening_balances=[0.0] * 4,
        depreciation_rates=[0.20] * 4,
    )
    out = calculate(inp)
    assert len(out.annual_loss_pool) == 6
    # Loss pool should be zero by end (enough income to offset)
    assert out.annual_loss_pool[-1] == pytest.approx(0.0)
    assert out.total_lifetime_tax > 0


# ---------------------------------------------------------------------------
# Bucket padding
# ---------------------------------------------------------------------------

def test_bucket_padding():
    inp = _make(capex_year1=100_000.0, n_buckets=4)
    assert len(inp.capex_by_bucket) == 7
    assert len(inp.opening_balances) == 7
    assert len(inp.depreciation_rates) == 7


# ---------------------------------------------------------------------------
# Interest fully deductible
# ---------------------------------------------------------------------------

def test_interest_fully_deductible():
    out = calculate(_make(ebitda_annual=100_000.0, interest_annual=40_000.0))
    # Taxable = 100k - 40k = 60k. Tax = 60k * 0.30 = 18k
    assert sum(out.annual_tax_charge) == pytest.approx(18_000.0, rel=1e-4)


# ---------------------------------------------------------------------------
# Loss carry-forward inactive
# ---------------------------------------------------------------------------

def test_loss_cfwd_inactive():
    n = 24
    ebitda = [-50_000.0 / 12] * 12 + [80_000.0 / 12] * 12
    interest = [0.0] * n
    inp = Inputs(
        periods=n, start_year=2028, start_month=1, tax_rate=0.30,
        loss_cfwd_active=False,
        ebitda=ebitda, total_interest=interest,
        capex_by_bucket=[[0.0] * n for _ in range(4)],
        opening_balances=[0.0] * 4,
        depreciation_rates=[0.25] * 4,
    )
    out = calculate(inp)
    # No loss offset -> year 2 taxable = 80k, tax = 24k
    assert sum(out.annual_tax_charge) == pytest.approx(80_000.0 * 0.30, rel=1e-4)


# ---------------------------------------------------------------------------
# First year no PAYG
# ---------------------------------------------------------------------------

def test_first_year_no_payg():
    """Construction year (first year) should have no PAYG payments."""
    out = calculate(_make(periods=36, ebitda_annual=100_000.0, interest_annual=0.0))
    # Year 0 (index 0) tax should not generate PAYG in the same year
    # First 12 periods: no PAYG expected since first year has no prior-year tax
    first_year_paid = sum(out.tax_paid[:12])
    assert first_year_paid == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_excel_formulas():
    refs = {
        "basis": "C10", "dep_rate": "B5",
        "ebitda": "H20", "annual_dep": "H23", "interest": "H21",
        "taxable_income": "H24", "tax_rate": "B8",
        "prior_year_tax": "H30",
    }
    formulas = get_excel_formulas(refs)
    for key in ("depreciation", "closing_balance", "taxable_income", "tax_charge", "payg_quarterly"):
        assert key in formulas
        assert formulas[key].startswith("=")
