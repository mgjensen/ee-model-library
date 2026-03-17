"""Tests for BESS_REPOW_001 — BESS battery stack repowering."""

import pytest
from modules.capex.BESS_REPOW_001 import Inputs, Outputs, calculate, get_excel_formulas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**overrides):
    defaults = dict(
        active=True,
        periods=360,           # 30 years
        start_year=2026,
        start_month=1,
        bess_cod_year=2026,
        bess_cod_month=11,
        repowering_years_after_cod=20,
        bess_capacity_MWh=312.0,
        repowering_cost_per_MWh_DKKk=94.0,  # ~29,328 DKKk total
        tax_depreciation_method="declining_balance",
        tax_depreciation_rate=0.25,
        tax_depreciation_lifetime_years=25,
        accounting_lifetime_years=10,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_invalid_tax_method():
    with pytest.raises(ValueError, match="tax_depreciation_method"):
        _make_inputs(tax_depreciation_method="invalid")


def test_validation_negative_capacity_rejected():
    with pytest.raises(ValueError):
        _make_inputs(bess_capacity_MWh=-10.0)


def test_validation_zero_cost_rejected():
    with pytest.raises(ValueError):
        _make_inputs(repowering_cost_per_MWh_DKKk=0.0)


# ---------------------------------------------------------------------------
# Inactive / outside project
# ---------------------------------------------------------------------------

def test_inactive_returns_zeros():
    out = calculate(_make_inputs(active=False))
    assert out.repowering_month == -1
    assert out.repowering_cost_total_DKKk == 0.0
    assert all(v == 0.0 for v in out.repowering_cost_monthly)
    assert all(v == 0.0 for v in out.tax_depreciation_monthly)


def test_repowering_outside_project_returns_zeros():
    """Repowering at year 20 in a 10-year model -> outside."""
    out = calculate(_make_inputs(periods=120))  # 10 years only
    assert out.repowering_month == -1
    assert out.repowering_cost_total_DKKk == 0.0


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_output_is_outputs():
    out = calculate(_make_inputs())
    assert isinstance(out, Outputs)


def test_output_array_lengths():
    n = 360
    out = calculate(_make_inputs(periods=n))
    for field in ("repowering_cost_monthly", "pre_repowering_flag",
                   "post_repowering_flag", "tax_depreciation_monthly",
                   "tax_asset_base_eop", "accounting_depreciation_monthly",
                   "accounting_asset_base_eop"):
        assert len(getattr(out, field)) == n, f"{field} length mismatch"


# ---------------------------------------------------------------------------
# Cost spike
# ---------------------------------------------------------------------------

def test_cost_spike_in_correct_period():
    out = calculate(_make_inputs())
    # COD Nov 2026, +20 years -> Nov 2046
    # Period = (2046-2026)*12 + (11-1) = 240+10 = 250
    assert out.repowering_month == 250
    assert out.repowering_cost_monthly[250] == pytest.approx(312.0 * 94.0)
    # All other periods zero
    assert all(out.repowering_cost_monthly[p] == 0.0 for p in range(360) if p != 250)


def test_cost_total():
    out = calculate(_make_inputs())
    assert out.repowering_cost_total_DKKk == pytest.approx(312.0 * 94.0)


# ---------------------------------------------------------------------------
# Operation flags
# ---------------------------------------------------------------------------

def test_pre_post_flags_sum():
    """Pre + post should cover all BESS operational months exactly once."""
    out = calculate(_make_inputs())
    # COD period = (2026-2026)*12 + (11-1) = 10
    for p in range(10):
        assert out.pre_repowering_flag[p] == 0.0
        assert out.post_repowering_flag[p] == 0.0
    for p in range(10, 250):
        assert out.pre_repowering_flag[p] == 1.0
        assert out.post_repowering_flag[p] == 0.0
    for p in range(250, 360):
        assert out.pre_repowering_flag[p] == 0.0
        assert out.post_repowering_flag[p] == 1.0


def test_flags_no_overlap():
    out = calculate(_make_inputs())
    for p in range(360):
        assert out.pre_repowering_flag[p] + out.post_repowering_flag[p] <= 1.0


# ---------------------------------------------------------------------------
# Tax depreciation — declining balance
# ---------------------------------------------------------------------------

def test_tax_dep_declining_year1():
    """First 12 months: base * 0.25 / 12 each month."""
    out = calculate(_make_inputs())
    total_cost = 312.0 * 94.0
    # Period 250: first month
    first_dep = total_cost * 0.25 / 12.0
    assert out.tax_depreciation_monthly[250] == pytest.approx(first_dep, rel=1e-6)


def test_tax_dep_declining_year5():
    """After ~60 months, base should be lower -> smaller depreciation."""
    out = calculate(_make_inputs())
    total_cost = 312.0 * 94.0
    # After 60 months of declining balance: base = cost * (1 - 0.25/12)^60
    base_60 = total_cost * (1 - 0.25 / 12.0) ** 60
    expected_dep = base_60 * 0.25 / 12.0
    assert out.tax_depreciation_monthly[310] == pytest.approx(expected_dep, rel=1e-4)


def test_tax_base_converges():
    """Tax base should decrease monotonically after repowering."""
    out = calculate(_make_inputs())
    rp = out.repowering_month
    # Only check non-zero portion
    bases = [out.tax_asset_base_eop[p] for p in range(rp, 360) if out.tax_asset_base_eop[p] > 0]
    for i in range(1, len(bases)):
        assert bases[i] <= bases[i - 1] + 0.01


# ---------------------------------------------------------------------------
# Tax depreciation — straight-line
# ---------------------------------------------------------------------------

def test_tax_dep_straight_line():
    out = calculate(_make_inputs(tax_depreciation_method="straight_line"))
    total_cost = 312.0 * 94.0
    expected = total_cost / (25 * 12)
    rp = out.repowering_month
    # First month
    assert out.tax_depreciation_monthly[rp] == pytest.approx(expected, rel=1e-6)
    # All months within lifetime are equal
    for p in range(rp, min(rp + 25 * 12, 360)):
        assert out.tax_depreciation_monthly[p] == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# Accounting depreciation
# ---------------------------------------------------------------------------

def test_accounting_dep_total_equals_cost():
    """Sum of accounting depreciation should equal total cost (if lifetime fits)."""
    # Use shorter project so accounting fits: 10yr acct, repow at period 10
    out = calculate(_make_inputs(
        bess_cod_year=2026, bess_cod_month=1,
        repowering_years_after_cod=1,
        periods=240,  # 20 years — enough for 10yr acct dep
        accounting_lifetime_years=10,
    ))
    total_dep = sum(out.accounting_depreciation_monthly)
    assert total_dep == pytest.approx(out.repowering_cost_total_DKKk, rel=1e-4)


def test_accounting_base_reaches_zero():
    """Accounting base should be ~0 at end of lifetime."""
    out = calculate(_make_inputs(
        bess_cod_year=2026, bess_cod_month=1,
        repowering_years_after_cod=1,
        periods=240,
        accounting_lifetime_years=10,
    ))
    rp = out.repowering_month
    end_period = rp + 10 * 12 - 1  # last period of acct dep
    assert out.accounting_asset_base_eop[end_period] < 0.01


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_repowering_first_period():
    """Repowering in period 0 (COD 20 years before project start)."""
    out = calculate(_make_inputs(
        start_year=2026, start_month=1,
        bess_cod_year=2006, bess_cod_month=1,
        repowering_years_after_cod=20,
        periods=240,
    ))
    assert out.repowering_month == 0
    assert out.repowering_cost_monthly[0] > 0
    assert out.post_repowering_flag[0] == 1.0


def test_repowering_last_period():
    """Repowering in the very last period."""
    # start=2026-1, cod=2026-1, years=1 -> repow 2027-1 = period 12
    out = calculate(_make_inputs(
        start_year=2026, start_month=1,
        bess_cod_year=2026, bess_cod_month=1,
        repowering_years_after_cod=1,
        periods=13,  # periods 0-12, repow at 12
    ))
    assert out.repowering_month == 12
    assert out.repowering_cost_monthly[12] > 0
    # Tax/acct dep only has 1 period
    assert out.tax_depreciation_monthly[12] > 0


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_dict():
    refs = {
        "period_ref": "C3",
        "repow_period": "250",
        "capacity": "$B$5",
        "cost_per_MWh": "$B$6",
        "prev_tax_base": "B31",
        "tax_rate": "$B$8",
        "total_cost": "$B$7",
        "tax_lifetime": "$B$9",
        "acct_lifetime": "$B$10",
        "cod_period": "$B$4",
    }
    formulas = get_excel_formulas(refs)
    assert isinstance(formulas, dict)
    assert "repowering_cost" in formulas
    assert "tax_dep_declining" in formulas
    assert "accounting_dep" in formulas
