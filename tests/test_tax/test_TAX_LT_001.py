"""Tests for TAX_LT_001 — Lithuanian corporate tax module."""

import pytest
from modules.tax.TAX_LT_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _year_groups,
    _calculate_straight_line_depreciation,
    _calculate_thin_cap,
    _calculate_tax_with_loss_cf,
    _apply_ebitda_cap,
    _apply_shl_interest_limit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    tax_rate=0.17,
    loss_cf_cap_pct=0.70,
    ebitda_annual=50_000.0,
    interest_annual=5_000.0,
    capex_year1=100_000.0,
    lifetimes=None,
    average_debt=None,
    average_equity=None,
    thin_cap_ratio=4.0,
):
    """Build a simple Inputs with flat monthly EBITDA and interest."""
    monthly_ebitda = [ebitda_annual / 12] * periods
    monthly_interest = [interest_annual / 12] * periods
    capex = [[0.0] * periods for _ in range(4)]
    capex[0][0] = capex_year1
    if lifetimes is None:
        lifetimes = [10, 10, 10, 10]
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        tax_rate=tax_rate,
        loss_cf_cap_pct=loss_cf_cap_pct,
        ebitda=monthly_ebitda,
        interest_expense=monthly_interest,
        capex_by_bucket=capex,
        opening_balances=[0.0, 0.0, 0.0, 0.0],
        depreciation_lifetimes=lifetimes,
        thin_cap_ratio=thin_cap_ratio,
        average_debt=average_debt if average_debt else [],
        average_equity=average_equity if average_equity else [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_rate_17pct():
    """Default Lithuanian tax rate is 17%."""
    out = calculate(_make_inputs(
        ebitda_annual=100_000.0,
        interest_annual=0.0,
        capex_year1=0.0,
    ))
    # Year 1 taxable = 100,000; tax = 17,000
    # Two years: sum ~34,000
    expected = 100_000.0 * 0.17 * 2
    assert sum(out.tax_charge_accrued) == pytest.approx(expected, rel=1e-4)


def test_straight_line_depreciation():
    """100,000 capex with 10-year lifetime → 10,000/year depreciation."""
    yg = _year_groups(24, 2025, 1)
    capex = [[0.0] * 24 for _ in range(7)]
    capex[0][0] = 100_000.0
    lifetimes = [10, 10, 10, 10, 10, 10, 10]
    annual_dep, monthly_dep = _calculate_straight_line_depreciation(
        [0.0] * 7, capex, lifetimes, yg, 24
    )
    assert annual_dep[0] == pytest.approx(10_000.0)
    # Year 2: basis = 100,000 - 10,000 = 90,000; dep = 90,000/10 = 9,000
    assert annual_dep[1] == pytest.approx(9_000.0)
    # Monthly spread: 10,000/12 per month in year 1
    assert monthly_dep[0] == pytest.approx(10_000.0 / 12)


def test_loss_cfwd_70pct_cap():
    """Loss carried forward is capped at 70% of taxable income."""
    # Year 0: loss of 100,000 → pool = 100,000
    # Year 1: gross taxable = 50,000 → max offset = 35,000 (70%)
    # → net taxable = 15,000 → tax = 15,000 × 0.17 = 2,550
    taxable, charge, pool = _calculate_tax_with_loss_cf(
        annual_ebitda=[-100_000.0, 50_000.0],
        annual_depreciation=[0.0, 0.0],
        annual_deductible_interest=[0.0, 0.0],
        tax_rate=0.17,
        loss_cf_cap_pct=0.70,
    )
    assert taxable[1] == pytest.approx(15_000.0)
    assert charge[1] == pytest.approx(15_000.0 * 0.17)
    # Remaining pool = 100,000 - 35,000 = 65,000
    assert pool[1] == pytest.approx(65_000.0)


def test_thin_cap_4to1():
    """D/E ratio > 4.0 → excess interest is non-deductible."""
    yg = _year_groups(12, 2025, 1)
    # avg_debt = 50,000; avg_equity = 10,000 → D/E = 5.0 > 4.0
    annual_interest = [12_000.0]
    avg_debt = [50_000.0] * 12
    avg_equity = [10_000.0] * 12
    ded, non_ded = _calculate_thin_cap(
        annual_interest, avg_debt, avg_equity, 4.0, yg
    )
    # Allowed debt = 10,000 × 4.0 = 40,000; allowed frac = 40,000/50,000 = 0.8
    assert ded[0] == pytest.approx(12_000.0 * 0.8)
    assert non_ded[0] == pytest.approx(12_000.0 * 0.2)


def test_no_thin_cap_when_below():
    """D/E ratio <= 4.0 → all interest deductible."""
    yg = _year_groups(12, 2025, 1)
    annual_interest = [10_000.0]
    avg_debt = [30_000.0] * 12
    avg_equity = [10_000.0] * 12
    ded, non_ded = _calculate_thin_cap(
        annual_interest, avg_debt, avg_equity, 4.0, yg
    )
    assert ded[0] == pytest.approx(10_000.0)
    assert non_ded[0] == pytest.approx(0.0)


def test_zero_ebitda_no_tax():
    """Zero EBITDA and no capex → no tax."""
    out = calculate(_make_inputs(
        ebitda_annual=0.0, interest_annual=0.0, capex_year1=0.0,
    ))
    assert out.total_tax_paid == pytest.approx(0.0)


def test_negative_ebitda_loss():
    """Negative EBITDA creates loss pool, no tax charged."""
    out = calculate(_make_inputs(
        ebitda_annual=-50_000.0, interest_annual=0.0, capex_year1=0.0,
    ))
    assert all(v == pytest.approx(0.0) for v in out.tax_paid)
    # DTA should be positive (loss pool × tax_rate)
    assert any(v > 0 for v in out.deferred_tax_asset)


def test_loss_offset_capped():
    """With large loss pool, offset cannot exceed 70% of taxable income."""
    taxable, charge, pool = _calculate_tax_with_loss_cf(
        annual_ebitda=[-200_000.0, 100_000.0],
        annual_depreciation=[0.0, 0.0],
        annual_deductible_interest=[0.0, 0.0],
        tax_rate=0.17,
        loss_cf_cap_pct=0.70,
    )
    # Year 1 offset = min(200,000, 100,000 × 0.70) = 70,000
    # Net taxable = 30,000
    assert taxable[1] == pytest.approx(30_000.0)
    assert charge[1] == pytest.approx(30_000.0 * 0.17)


def test_tax_payment_timing():
    """Tax is paid in month 6 of the following year."""
    out = calculate(_make_inputs(
        periods=36, start_year=2025, start_month=1,
        ebitda_annual=100_000.0, interest_annual=0.0, capex_year1=0.0,
    ))
    for p, val in enumerate(out.tax_paid):
        if abs(val) > 1e-9:
            # Determine calendar month of this period
            month = ((1 - 1 + p) % 12) + 1
            assert month == 6, f"Unexpected payment in month {month} (period {p})"


def test_deferred_tax_asset():
    """DTA = loss pool × tax_rate when there are losses."""
    out = calculate(_make_inputs(
        ebitda_annual=-50_000.0, interest_annual=0.0, capex_year1=0.0,
    ))
    # Loss pool > 0 → DTA > 0
    assert any(v > 0 for v in out.deferred_tax_asset)
    # All DTAs should be non-negative
    assert all(v >= -1e-9 for v in out.deferred_tax_asset)


def test_output_lengths():
    """All output arrays have length == periods."""
    n = 36
    out = calculate(_make_inputs(periods=n))
    for field in (
        "tax_depreciation", "deductible_interest", "non_deductible_interest",
        "taxable_income", "loss_carried_forward", "tax_charge_accrued",
        "tax_paid", "deferred_tax_asset", "deferred_tax_liability",
    ):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


def test_validation():
    """Wrong array lengths raise ValueError."""
    with pytest.raises(ValueError, match="ebitda length"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            ebitda=[0.0] * 10,
            interest_expense=[0.0] * 12,
            capex_by_bucket=[[0.0] * 12 for _ in range(4)],
            depreciation_lifetimes=[10, 10, 10, 10],
        )

    with pytest.raises(ValueError, match="interest_expense length"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            ebitda=[0.0] * 12,
            interest_expense=[0.0] * 8,
            capex_by_bucket=[[0.0] * 12 for _ in range(4)],
            depreciation_lifetimes=[10, 10, 10, 10],
        )


def test_multiple_buckets():
    """Multiple buckets with different lifetimes depreciate correctly."""
    yg = _year_groups(12, 2025, 1)
    capex = [[0.0] * 12 for _ in range(7)]
    capex[0][0] = 100_000.0  # lifetime 10
    capex[1][0] = 50_000.0   # lifetime 5
    lifetimes = [10, 5, 10, 10, 10, 10, 10]
    annual_dep, _ = _calculate_straight_line_depreciation(
        [0.0] * 7, capex, lifetimes, yg, 12
    )
    # Bucket 0: 100,000/10 = 10,000; Bucket 1: 50,000/5 = 10,000; total = 20,000
    assert annual_dep[0] == pytest.approx(20_000.0)


def test_total_tax_scalar():
    """total_tax_paid equals sum of tax_paid array."""
    out = calculate(_make_inputs())
    assert out.total_tax_paid == pytest.approx(sum(out.tax_paid))


def test_excel_formulas():
    """get_excel_formulas returns dict with expected keys and formula strings."""
    refs = {
        "opening_bal": "C10", "capex": "C11", "lifetime": "B5",
        "ebitda": "H20", "interest": "H21", "annual_dep": "H23",
        "deductible_int": "H22", "taxable_income": "H24", "tax_rate": "B8",
        "avg_debt": "H30", "avg_equity": "H31", "thin_cap_ratio": "B9",
        "loss_pool": "H25", "loss_cap_pct": "B10",
    }
    formulas = get_excel_formulas(refs)
    for key in ("straight_line_dep", "closing_balance", "thin_cap_check",
                "non_deductible_interest", "taxable_income_gross",
                "loss_offset", "tax_charge"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# EBITDA cap tests
# ---------------------------------------------------------------------------

def test_ebitda_cap_basic():
    """30% EBITDA cap limits deductible interest."""
    # EBITDA = 100,000; 30% = 30,000; interest = 50,000
    # Deductible = max(floor=3000, 30000) = 30,000; non-ded = 20,000
    ded, non_ded = _apply_ebitda_cap(
        annual_deductible=[50_000.0],
        annual_ebitda=[100_000.0],
        ebitda_cap_pct=0.30,
        floor_DKKk=3_000.0,
    )
    assert ded[0] == pytest.approx(30_000.0)
    assert non_ded[0] == pytest.approx(20_000.0)


def test_ebitda_cap_floor():
    """Floor ensures minimum deductibility even with low EBITDA."""
    # EBITDA = 5,000; 30% = 1,500; floor = 3,000
    # Allowed = max(3000, 1500) = 3,000; interest = 4,000
    ded, non_ded = _apply_ebitda_cap(
        annual_deductible=[4_000.0],
        annual_ebitda=[5_000.0],
        ebitda_cap_pct=0.30,
        floor_DKKk=3_000.0,
    )
    assert ded[0] == pytest.approx(3_000.0)
    assert non_ded[0] == pytest.approx(1_000.0)


def test_ebitda_cap_under_limit():
    """Interest below cap is fully deductible."""
    ded, non_ded = _apply_ebitda_cap(
        annual_deductible=[10_000.0],
        annual_ebitda=[100_000.0],
        ebitda_cap_pct=0.30,
        floor_DKKk=3_000.0,
    )
    assert ded[0] == pytest.approx(10_000.0)
    assert non_ded[0] == pytest.approx(0.0)


def test_ebitda_cap_negative_ebitda():
    """Negative EBITDA falls back to floor."""
    ded, non_ded = _apply_ebitda_cap(
        annual_deductible=[5_000.0],
        annual_ebitda=[-50_000.0],
        ebitda_cap_pct=0.30,
        floor_DKKk=3_000.0,
    )
    assert ded[0] == pytest.approx(3_000.0)
    assert non_ded[0] == pytest.approx(2_000.0)


def test_ebitda_cap_e2e():
    """EBITDA cap reduces tax charge via higher non-deductible interest."""
    # Without cap
    out_no_cap = calculate(_make_inputs(
        ebitda_annual=100_000.0, interest_annual=50_000.0, capex_year1=0.0,
    ))
    # With cap: 30% of 100k = 30k < 50k interest → 20k non-deductible
    inp = _make_inputs(
        ebitda_annual=100_000.0, interest_annual=50_000.0, capex_year1=0.0,
    )
    inp_capped = inp.model_copy(update={
        "ebitda_cap_active": True,
        "ebitda_cap_pct": 0.30,
        "ebitda_cap_floor_DKKk": 3_000.0,
    })
    out_capped = calculate(inp_capped)
    # With cap: less interest deducted → higher taxable → more tax
    assert sum(out_capped.tax_charge_accrued) > sum(out_no_cap.tax_charge_accrued)
    assert sum(out_capped.non_deductible_interest) > sum(out_no_cap.non_deductible_interest)


# ---------------------------------------------------------------------------
# SHL interest limit tests
# ---------------------------------------------------------------------------

def test_shl_interest_limit_basic():
    """SHL interest capped at 3,000 kEUR p.a."""
    adj, non_ded = _apply_shl_interest_limit(
        annual_interest=[20_000.0],
        annual_shl_interest=[10_000.0],
        limit_DKKk=3_000.0,
    )
    # SHL: 10,000 → capped at 3,000; non-SHL: 10,000 unchanged
    assert adj[0] == pytest.approx(10_000.0 + 3_000.0)
    assert non_ded[0] == pytest.approx(7_000.0)


def test_shl_interest_limit_under():
    """SHL interest below limit is fully preserved."""
    adj, non_ded = _apply_shl_interest_limit(
        annual_interest=[5_000.0],
        annual_shl_interest=[2_000.0],
        limit_DKKk=3_000.0,
    )
    assert adj[0] == pytest.approx(5_000.0)
    assert non_ded[0] == pytest.approx(0.0)


def test_shl_interest_limit_e2e():
    """SHL interest limit increases non-deductible interest in full calculation."""
    n = 24
    shl_interest = [500.0] * n  # 6,000/year > 3,000 limit
    inp = _make_inputs(
        periods=n, ebitda_annual=100_000.0,
        interest_annual=12_000.0, capex_year1=0.0,
    )
    inp_lim = inp.model_copy(update={
        "shl_interest_limit_active": True,
        "shl_interest_limit_DKKk": 3_000.0,
        "shl_interest_expense": shl_interest,
    })
    out = calculate(inp_lim)
    # 6,000 SHL/yr → 3,000 non-ded/yr → non_ded_interest should be positive
    assert sum(out.non_deductible_interest) > 0


# ---------------------------------------------------------------------------
# Unlevered tax tests
# ---------------------------------------------------------------------------

def test_unlevered_tax_exists():
    """Outputs include unlevered_tax_charge_accrued."""
    out = calculate(_make_inputs())
    assert hasattr(out, "unlevered_tax_charge_accrued")
    assert hasattr(out, "annual_unlevered_tax_charge")


def test_unlevered_tax_higher():
    """Unlevered tax >= levered tax (no interest benefit)."""
    out = calculate(_make_inputs(
        ebitda_annual=100_000.0, interest_annual=20_000.0, capex_year1=0.0,
    ))
    lev = sum(out.tax_charge_accrued)
    unlev = sum(out.unlevered_tax_charge_accrued)
    assert unlev >= lev - 0.01


def test_unlevered_tax_equal_when_no_interest():
    """With zero interest, levered == unlevered."""
    out = calculate(_make_inputs(
        ebitda_annual=100_000.0, interest_annual=0.0, capex_year1=0.0,
    ))
    lev = sum(out.tax_charge_accrued)
    unlev = sum(out.unlevered_tax_charge_accrued)
    assert unlev == pytest.approx(lev, rel=1e-6)


def test_unlevered_tax_output_length():
    """Unlevered outputs have correct lengths."""
    n = 36
    out = calculate(_make_inputs(periods=n))
    assert len(out.unlevered_tax_charge_accrued) == n
    # annual array: number of years spanned
    yg = _year_groups(n, 2025, 1)
    assert len(out.annual_unlevered_tax_charge) == len(yg)
