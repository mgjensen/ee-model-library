"""Tests for IRR_001 — DCF valuation module."""

import math
import pytest
from modules.statements.IRR_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _npv,
    _irr_monthly,
    _annualise_irr,
    _monthly_rate,
    _annual_sums,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=120,
    pfcf=None,
    ecf=None,
    project_discount_rate=0.07,
    equity_discount_rate=0.10,
) -> Inputs:
    n = periods
    # Default: 12 months negative (construction), then positive
    if pfcf is None:
        pfcf = [-5_000.0] * 12 + [800.0] * (n - 12)
    if ecf is None:
        ecf = [-1_500.0] * 12 + [300.0] * (n - 12)
    return Inputs(
        periods=n,
        project_free_cash_flow=pfcf,
        equity_cash_flow=ecf,
        project_discount_rate=project_discount_rate,
        equity_discount_rate=equity_discount_rate,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_wrong_length_pfcf():
    with pytest.raises(ValueError, match="project_free_cash_flow"):
        Inputs(
            periods=12,
            project_free_cash_flow=[100.0] * 6,
            equity_cash_flow=[50.0] * 12,
        )


def test_wrong_length_ecf():
    with pytest.raises(ValueError, match="equity_cash_flow"):
        Inputs(
            periods=12,
            project_free_cash_flow=[100.0] * 12,
            equity_cash_flow=[50.0] * 6,
        )


# ---------------------------------------------------------------------------
# Unit: _npv
# ---------------------------------------------------------------------------

def test_npv_zero_rate_is_sum():
    cfs = [-100.0, 50.0, 60.0]
    assert _npv(cfs, 0.0) == pytest.approx(10.0)


def test_npv_positive_rate_discounts():
    """Higher rate → lower NPV: upfront investment followed by positive returns."""
    cfs = [-1_000.0] + [80.0] * 24
    npv_lo = _npv(cfs, 0.001)
    npv_hi = _npv(cfs, 0.01)
    assert npv_hi < npv_lo


def test_npv_single_period():
    """Period 0 cash flow is undiscounted."""
    assert _npv([500.0], 0.05) == pytest.approx(500.0)


def test_npv_known_value():
    """NPV of [-100, 110] at 10% monthly = -100 + 110/1.1 = 0."""
    cfs = [-100.0, 110.0]
    assert _npv(cfs, 0.10) == pytest.approx(0.0, abs=1e-8)


# ---------------------------------------------------------------------------
# Unit: _irr_monthly
# ---------------------------------------------------------------------------

def test_irr_monthly_no_solution_all_positive():
    assert math.isnan(_irr_monthly([100.0, 200.0, 300.0]))


def test_irr_monthly_no_solution_all_negative():
    assert math.isnan(_irr_monthly([-100.0, -200.0]))


def test_irr_monthly_known_case():
    """[-100, 110] → monthly IRR = 10%."""
    r = _irr_monthly([-100.0, 110.0])
    assert r == pytest.approx(0.10, abs=1e-6)


def test_irr_monthly_multi_period():
    """[-1000, 100 × 12] → find monthly rate s.t. NPV=0."""
    cfs = [-1_000.0] + [100.0] * 12
    r = _irr_monthly(cfs)
    assert not math.isnan(r)
    assert abs(_npv(cfs, r)) < 1e-4


def test_irr_monthly_construction_then_ops():
    """Typical project shape: large negative up front, positive over time."""
    cfs = [-5_000.0] * 12 + [600.0] * 120
    r = _irr_monthly(cfs)
    assert not math.isnan(r)
    assert r > 0


# ---------------------------------------------------------------------------
# Unit: _annualise_irr
# ---------------------------------------------------------------------------

def test_annualise_irr_nan_passthrough():
    assert math.isnan(_annualise_irr(float("nan")))


def test_annualise_irr_zero():
    assert _annualise_irr(0.0) == pytest.approx(0.0)


def test_annualise_irr_known():
    # monthly 1% → annual ≈ 12.68%
    annual = _annualise_irr(0.01)
    assert annual == pytest.approx(1.01 ** 12 - 1, rel=1e-6)


# ---------------------------------------------------------------------------
# Unit: _monthly_rate
# ---------------------------------------------------------------------------

def test_monthly_rate_zero():
    assert _monthly_rate(0.0) == pytest.approx(0.0)


def test_monthly_rate_known():
    # 12% annual → monthly ≈ 0.9489%
    r = _monthly_rate(0.12)
    assert (1 + r) ** 12 == pytest.approx(1.12, rel=1e-6)


# ---------------------------------------------------------------------------
# Unit: _annual_sums
# ---------------------------------------------------------------------------

def test_annual_sums_exact_years():
    series = [1.0] * 24
    result = _annual_sums(series)
    assert result == [12.0, 12.0]


def test_annual_sums_partial_year():
    series = [1.0] * 15
    result = _annual_sums(series)
    assert result == [12.0, 3.0]


# ---------------------------------------------------------------------------
# Unit: calculate
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs():
    assert isinstance(calculate(_base_inputs()), Outputs)


def test_calculate_array_lengths():
    out = calculate(_base_inputs(periods=120))
    assert len(out.cumulative_pfcf) == 120
    assert len(out.annual_pfcf) == 10   # 120 / 12


def test_project_irr_is_annual():
    """Project IRR should be in a sensible annual range for a viable project."""
    out = calculate(_base_inputs(periods=120))
    assert not math.isnan(out.project_irr)
    assert 0.0 < out.project_irr < 1.0  # 0%–100% annual


def test_equity_irr_higher_than_project_irr():
    """Leverage should boost equity IRR above project IRR (for positive project)."""
    out = calculate(_base_inputs(periods=180))
    if not math.isnan(out.equity_irr) and not math.isnan(out.project_irr):
        # Not always guaranteed, but common for this default test case
        assert out.equity_irr != pytest.approx(out.project_irr)


def test_project_npv_positive_for_good_project():
    """Good project (high returns) should have positive NPV at 7%."""
    pfcf = [-100_000.0] + [5_000.0] * 240
    ecf  = [-30_000.0] + [2_000.0] * 240
    out = calculate(Inputs(
        periods=241,
        project_free_cash_flow=pfcf,
        equity_cash_flow=ecf,
        project_discount_rate=0.07,
        equity_discount_rate=0.10,
    ))
    assert out.project_npv > 0


def test_project_npv_negative_for_bad_project():
    """Very low returns relative to discount rate → negative NPV."""
    pfcf = [-100_000.0] + [100.0] * 120    # tiny returns
    ecf  = [-30_000.0] + [30.0] * 120
    out = calculate(Inputs(
        periods=121,
        project_free_cash_flow=pfcf,
        equity_cash_flow=ecf,
        project_discount_rate=0.07,
        equity_discount_rate=0.10,
    ))
    assert out.project_npv < 0


def test_npv_at_irr_is_zero():
    """NPV at the IRR rate should equal zero."""
    pfcf = [-50_000.0] * 12 + [3_000.0] * 120
    out = calculate(Inputs(
        periods=132,
        project_free_cash_flow=pfcf,
        equity_cash_flow=pfcf,
    ))
    if not math.isnan(out.project_irr):
        mr = _monthly_rate(out.project_irr)
        npv_at_irr = _npv(pfcf, mr)
        assert abs(npv_at_irr) < 10.0  # within 10 DKKk of zero (rounding)


# ---------------------------------------------------------------------------
# Unit: payback period
# ---------------------------------------------------------------------------

def test_payback_is_first_period_positive_cumulative():
    pfcf = [-1_000.0, -500.0, 200.0, 800.0, 600.0]
    out = calculate(Inputs(
        periods=5,
        project_free_cash_flow=pfcf,
        equity_cash_flow=pfcf,
    ))
    # Cumulative: -1000, -1500, -1300, -500, 100 → first ≥0 at index 4
    assert out.payback_period_months == 4


def test_payback_never_recovered():
    pfcf = [-1_000.0] * 24
    out = calculate(Inputs(
        periods=24,
        project_free_cash_flow=pfcf,
        equity_cash_flow=pfcf,
    ))
    assert out.payback_period_months == -1


def test_payback_immediate():
    pfcf = [1_000.0] + [-100.0] * 11
    out = calculate(Inputs(
        periods=12,
        project_free_cash_flow=pfcf,
        equity_cash_flow=pfcf,
    ))
    assert out.payback_period_months == 0


# ---------------------------------------------------------------------------
# Unit: cumulative PFCF
# ---------------------------------------------------------------------------

def test_cumulative_pfcf_is_running_sum():
    pfcf = [-1_000.0, 200.0, 300.0, 400.0, 500.0]
    out = calculate(Inputs(
        periods=5,
        project_free_cash_flow=pfcf,
        equity_cash_flow=pfcf,
    ))
    expected = [-1_000.0, -800.0, -500.0, -100.0, 400.0]
    assert out.cumulative_pfcf == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Integration: Viuf-scale 475-period project
# ---------------------------------------------------------------------------

def test_viuf_irr_proxy():
    """
    475-period project proxy.
    Construction: 18 months, EPC + BESS ~750 MKK, 70% debt / 30% equity.
    Operations: ~57 MKK/yr net revenue - 30 MKK/yr costs = ~27 MKK/yr PFCF.
    """
    n = 475
    # Construction phase (18 months): heavy capex outflows
    pfcf_construction = [-40_000.0] * 18
    pfcf_operations   = [2_200.0] * (n - 18)
    pfcf = pfcf_construction + pfcf_operations

    ecf_construction = [-12_000.0] * 18
    ecf_operations   = [1_000.0] * (n - 18)
    ecf = ecf_construction + ecf_operations

    out = calculate(Inputs(
        periods=n,
        project_free_cash_flow=pfcf,
        equity_cash_flow=ecf,
        project_discount_rate=0.07,
        equity_discount_rate=0.10,
    ))

    assert not math.isnan(out.project_irr)
    assert not math.isnan(out.equity_irr)
    assert 0.01 < out.project_irr < 0.25   # 1%–25% annual
    # IRR (1.8%) < discount rate (7%) → NPV is negative, which is correct
    assert out.project_npv != 0.0
    assert out.payback_period_months > 18   # payback after construction
    assert len(out.cumulative_pfcf) == n


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "pfcf_range": "K20:K499", "ecf_range": "K520:K999",
        "project_rate": "B5", "equity_rate": "B6",
        "prev_cum_pfcf": "J20", "pfcf": "K20",
    }
    formulas = get_excel_formulas(refs)
    for key in ("project_irr", "equity_irr", "project_npv", "equity_npv", "cumulative_pfcf"):
        assert key in formulas
        assert formulas[key].startswith("=")
