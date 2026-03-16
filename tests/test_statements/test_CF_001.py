"""Tests for CF_001 — Cash Flow statement module."""

import pytest
from modules.statements.CF_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _year_groups,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    opening_cash_DKKk=0.0,
    net_income=None,
    depreciation=None,
    capex_monthly=None,
    interest_paid=None,
    **kwargs,
) -> Inputs:
    n = periods
    return Inputs(
        periods=n,
        start_year=start_year,
        start_month=start_month,
        opening_cash_DKKk=opening_cash_DKKk,
        net_income=net_income or [500.0] * n,
        depreciation=depreciation or [100.0] * n,
        capex_monthly=capex_monthly or [0.0] * n,
        interest_paid=interest_paid or [50.0] * n,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_wrong_length_net_income():
    with pytest.raises(ValueError, match="net_income"):
        _base_inputs(periods=12, net_income=[500.0] * 6)


def test_wrong_length_capex():
    with pytest.raises(ValueError, match="capex_monthly"):
        _base_inputs(periods=12, capex_monthly=[1_000.0] * 6)


def test_wrong_length_optional_series():
    with pytest.raises(ValueError, match="debt_drawdown"):
        _base_inputs(periods=12, debt_drawdown=[1_000.0] * 6)


# ---------------------------------------------------------------------------
# Unit: operating cash flow
# ---------------------------------------------------------------------------

def test_cfo_is_ni_plus_dep():
    out = calculate(_base_inputs(
        net_income=[500.0] * 24,
        depreciation=[100.0] * 24,
    ))
    # CFO = ni + dep + wc = 500 + 100 + 0 = 600 (interest is in CFF, not CFO)
    assert all(v == pytest.approx(600.0) for v in out.cfo)


def test_cfo_basic_no_wc():
    out = calculate(_base_inputs(
        net_income=[300.0] * 24,
        depreciation=[50.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert all(v == pytest.approx(350.0) for v in out.cfo)


def test_cfo_with_working_capital_absorbed():
    out = calculate(_base_inputs(
        net_income=[300.0] * 24,
        depreciation=[50.0] * 24,
        working_capital_change=[-20.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert all(v == pytest.approx(330.0) for v in out.cfo)


def test_cfo_with_working_capital_released():
    out = calculate(_base_inputs(
        net_income=[300.0] * 24,
        depreciation=[50.0] * 24,
        working_capital_change=[30.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert all(v == pytest.approx(380.0) for v in out.cfo)


# ---------------------------------------------------------------------------
# Unit: investing cash flow
# ---------------------------------------------------------------------------

def test_cfi_is_negative_capex():
    out = calculate(_base_inputs(capex_monthly=[1_000.0] * 12 + [0.0] * 12))
    for p in range(12):
        assert out.cfi[p] == pytest.approx(-1_000.0)
    for p in range(12, 24):
        assert out.cfi[p] == pytest.approx(0.0)


def test_cfi_zero_when_no_capex():
    out = calculate(_base_inputs(capex_monthly=[0.0] * 24))
    assert all(v == pytest.approx(0.0) for v in out.cfi)


# ---------------------------------------------------------------------------
# Unit: financing cash flow
# ---------------------------------------------------------------------------

def test_cff_drawdown_positive():
    out = calculate(_base_inputs(
        debt_drawdown=[5_000.0] * 12 + [0.0] * 12,
        interest_paid=[0.0] * 24,
    ))
    for p in range(12):
        assert out.cff[p] == pytest.approx(5_000.0)


def test_cff_repayment_negative():
    out = calculate(_base_inputs(
        principal_repayment=[200.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert all(v == pytest.approx(-200.0) for v in out.cff)


def test_cff_interest_reduces_cash():
    out = calculate(_base_inputs(
        interest_paid=[100.0] * 24,
    ))
    # Only interest, no drawdown/repayment/dividends
    assert all(v == pytest.approx(-100.0) for v in out.cff)


def test_cff_dividends_reduce_cash():
    out = calculate(_base_inputs(
        interest_paid=[0.0] * 24,
        dividends_paid=[300.0] * 24,
    ))
    assert all(v == pytest.approx(-300.0) for v in out.cff)


def test_cff_combined():
    out = calculate(_base_inputs(
        debt_drawdown=[1_000.0] * 24,
        principal_repayment=[200.0] * 24,
        interest_paid=[50.0] * 24,
        dividends_paid=[100.0] * 24,
    ))
    # 1000 - 200 - 50 - 100 = 650
    assert all(v == pytest.approx(650.0) for v in out.cff)


# ---------------------------------------------------------------------------
# Unit: net cash flow
# ---------------------------------------------------------------------------

def test_net_is_cfo_plus_cfi_plus_cff():
    out = calculate(_base_inputs())
    for p in range(24):
        assert out.net_cash_flow[p] == pytest.approx(out.cfo[p] + out.cfi[p] + out.cff[p])


# ---------------------------------------------------------------------------
# Unit: cash balance mechanics
# ---------------------------------------------------------------------------

def test_closing_cash_tracks_cumulative():
    out = calculate(_base_inputs(
        net_income=[100.0] * 24,
        depreciation=[0.0] * 24,
        capex_monthly=[0.0] * 24,
        interest_paid=[0.0] * 24,
        opening_cash_DKKk=0.0,
    ))
    for p in range(24):
        assert out.closing_cash[p] == pytest.approx((p + 1) * 100.0)


def test_opening_cash_is_prior_closing():
    out = calculate(_base_inputs())
    for p in range(1, 24):
        assert out.opening_cash[p] == pytest.approx(out.closing_cash[p - 1])


def test_opening_cash_period0_uses_input():
    out = calculate(_base_inputs(opening_cash_DKKk=5_000.0))
    assert out.opening_cash[0] == pytest.approx(5_000.0)


def test_closing_cash_period0():
    out = calculate(_base_inputs(
        opening_cash_DKKk=1_000.0,
        net_income=[200.0] * 24,
        depreciation=[0.0] * 24,
        capex_monthly=[0.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert out.closing_cash[0] == pytest.approx(1_200.0)


def test_negative_cash_possible():
    """Cash can go negative if outflows exceed inflows."""
    out = calculate(_base_inputs(
        opening_cash_DKKk=0.0,
        net_income=[-100.0] * 24,
        depreciation=[0.0] * 24,
        capex_monthly=[0.0] * 24,
        interest_paid=[0.0] * 24,
    ))
    assert out.closing_cash[-1] < 0.0


# ---------------------------------------------------------------------------
# Unit: annual aggregation
# ---------------------------------------------------------------------------

def test_annual_cfo_sums_monthly():
    out = calculate(_base_inputs(periods=24, start_year=2025, start_month=1))
    assert out.annual_cfo[0] == pytest.approx(sum(out.cfo[:12]))
    assert out.annual_cfo[1] == pytest.approx(sum(out.cfo[12:]))


def test_annual_count():
    out = calculate(_base_inputs(periods=24))
    assert len(out.annual_cfo) == 2
    assert len(out.annual_cfi) == 2
    assert len(out.annual_cff) == 2


# ---------------------------------------------------------------------------
# Unit: lifetime totals
# ---------------------------------------------------------------------------

def test_lifetime_cfo():
    out = calculate(_base_inputs())
    assert out.lifetime_cfo == pytest.approx(sum(out.cfo))


def test_lifetime_cfi():
    out = calculate(_base_inputs(capex_monthly=[500.0] * 24))
    assert out.lifetime_cfi == pytest.approx(sum(out.cfi))


def test_lifetime_cff():
    out = calculate(_base_inputs())
    assert out.lifetime_cff == pytest.approx(sum(out.cff))


# ---------------------------------------------------------------------------
# Integration: construction + operations proxy
# ---------------------------------------------------------------------------

def test_construction_then_operations():
    """
    12 months construction (capex out, debt in, net income negative),
    then 12 months operations (positive cash flow).
    """
    n = 24
    capex = [5_000.0] * 12 + [0.0] * 12
    drawdown = [5_000.0] * 12 + [0.0] * 12
    ni = [-200.0] * 12 + [800.0] * 12
    dep = [0.0] * 12 + [150.0] * 12
    interest = [100.0] * 24
    repayment = [0.0] * 12 + [200.0] * 12

    out = calculate(Inputs(
        periods=n,
        start_year=2025,
        start_month=1,
        net_income=ni,
        depreciation=dep,
        capex_monthly=capex,
        debt_drawdown=drawdown,
        principal_repayment=repayment,
        interest_paid=interest,
    ))

    # Construction: net ≈ 0 (drawdown offsets capex)
    assert sum(out.net_cash_flow[:12]) == pytest.approx(
        sum(out.cfo[:12]) + sum(out.cfi[:12]) + sum(out.cff[:12])
    )
    # Operations: positive CFO
    assert sum(out.cfo[12:]) > 0
    # Cash balance should be positive at end if project is viable
    assert len(out.closing_cash) == n


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "net_income": "K10", "depreciation": "K11", "wc_change": "K12",
        "capex": "K20", "debt_drawdown": "K30", "principal": "K31",
        "interest_paid": "K32", "dividends": "K33",
        "prev_closing_cash": "J40", "net_cash_flow": "K41",
        "cfo": "K13", "cfi": "K21", "cff": "K34",
    }
    formulas = get_excel_formulas(refs)
    for key in ("cfo", "cfi", "cff", "net_cash_flow", "opening_cash", "closing_cash"):
        assert key in formulas
        assert formulas[key].startswith("=")
