"""Tests for DEBT_LINEAR_001 — multi-facility linear senior debt."""

import pytest
from modules.debt.DEBT_LINEAR_001 import (
    LinearFacility, Inputs, Outputs, FacilityResult, calculate, get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _even_drawdowns(facility, n_draw, total_periods):
    return [facility / n_draw] * n_draw + [0.0] * (total_periods - n_draw)


def _make_facility(
    name="Senior 1",
    facility=100_000.0,
    n_draw=12,
    periods=180,
    grace_periods=0,
    tenor_months=120,
    repayments_per_year=2,
    margin=0.016,
    base_rate=0.03,
    hedge_pct=0.0,
    swap_rate=None,
    arrangement_fee_pct=0.0,
    active=True,
):
    return LinearFacility(
        name=name,
        active=active,
        facility_DKKk=facility,
        start_period=0,
        grace_periods=grace_periods,
        tenor_months=tenor_months,
        repayments_per_year=repayments_per_year,
        margin=margin,
        base_rate=base_rate,
        hedge_pct=hedge_pct,
        swap_rate=swap_rate,
        arrangement_fee_pct=arrangement_fee_pct,
        drawdowns=_even_drawdowns(facility, n_draw, periods),
    )


def _make_inputs(facilities=None, periods=180):
    if facilities is None:
        facilities = [_make_facility(periods=periods)]
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        facilities=facilities,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_drawdowns_length():
    with pytest.raises(ValueError, match="drawdowns length"):
        _make_inputs(facilities=[
            LinearFacility(
                name="Bad", facility_DKKk=100.0, start_period=0,
                tenor_months=60, margin=0.01,
                drawdowns=[50.0, 50.0],  # wrong length
            )
        ], periods=10)


# ---------------------------------------------------------------------------
# Single facility basics
# ---------------------------------------------------------------------------

def test_single_facility_straight_line():
    """Known principal per repayment period."""
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    # Semi-annual, 120 months = 20 repayments
    # principal per repayment = 100,000 / 20 = 5,000
    principals = [r.principal[p] for p in range(180) if r.principal[p] > 0]
    assert all(p == pytest.approx(5000.0, abs=1.0) for p in principals)


def test_single_facility_fully_repaid():
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    # After tenor (12 construction + 120 repayment = 132), balance should be ~0
    assert r.closing_balance[131] < 1.0


def test_total_principal_equals_facility():
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    assert r.total_principal == pytest.approx(100_000.0, rel=0.01)


# ---------------------------------------------------------------------------
# Multi-facility
# ---------------------------------------------------------------------------

def test_inactive_facility_excluded():
    """3 facilities, 1 inactive -> aggregate = sum of 2 active."""
    facs = [
        _make_facility(name="F1", facility=50_000.0, periods=180),
        _make_facility(name="F2", facility=30_000.0, periods=180),
        _make_facility(name="F3", facility=20_000.0, periods=180, active=False),
    ]
    out = calculate(_make_inputs(facilities=facs))
    assert out.aggregate_facility_DKKk == pytest.approx(80_000.0)
    # Inactive facility has zero principal
    assert out.facility_results[2].total_principal == 0.0


def test_three_facilities_aggregate():
    """Totals = sum of individual."""
    facs = [
        _make_facility(name="F1", facility=50_000.0, periods=180),
        _make_facility(name="F2", facility=30_000.0, periods=180),
        _make_facility(name="F3", facility=20_000.0, periods=180),
    ]
    out = calculate(_make_inputs(facilities=facs))
    for p in range(180):
        expected_int = sum(r.interest[p] for r in out.facility_results)
        assert out.total_interest[p] == pytest.approx(expected_int, abs=0.01)
    assert out.aggregate_total_principal == pytest.approx(
        sum(r.total_principal for r in out.facility_results), rel=0.01
    )


def test_different_tenors_across_facilities():
    """Each facility has its own maturity."""
    facs = [
        _make_facility(name="Short", facility=50_000.0, tenor_months=60, periods=180),
        _make_facility(name="Long", facility=50_000.0, tenor_months=120, periods=180),
    ]
    out = calculate(_make_inputs(facilities=facs))
    # Short facility should be repaid earlier
    short = out.facility_results[0]
    long = out.facility_results[1]
    short_last = max((p for p in range(180) if short.closing_balance[p] > 0.01), default=0)
    long_last = max((p for p in range(180) if long.closing_balance[p] > 0.01), default=0)
    assert short_last < long_last


# ---------------------------------------------------------------------------
# Grace period
# ---------------------------------------------------------------------------

def test_grace_period_no_repayment():
    fac = _make_facility(grace_periods=6)
    out = calculate(_make_inputs(facilities=[fac]))
    r = out.facility_results[0]
    # Last drawdown at period 11, grace = 6 -> repayment starts at 18
    for p in range(18):
        assert r.principal[p] == 0.0


# ---------------------------------------------------------------------------
# Repayment frequency
# ---------------------------------------------------------------------------

def test_semi_annual_repayment_frequency():
    """Principal only every 6 months."""
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    # Start_month=1, semi-annual: payments in months 6 and 12 (Jun, Dec)
    for p in range(12, 132):
        cal_month = ((1 - 1 + p) % 12) + 1
        if cal_month in (6, 12):
            if r.opening_balance[p] > 1.0:
                assert r.principal[p] > 0, f"Expected principal at period {p}"
        else:
            assert r.principal[p] == 0.0, f"No principal expected at period {p}"


def test_quarterly_repayment_frequency():
    fac = _make_facility(repayments_per_year=4)
    out = calculate(_make_inputs(facilities=[fac]))
    r = out.facility_results[0]
    # Quarterly: payments months 3, 6, 9, 12
    repay_months = {3, 6, 9, 12}
    for p in range(12, 132):
        cal_month = ((1 - 1 + p) % 12) + 1
        if cal_month in repay_months and r.opening_balance[p] > 1.0:
            assert r.principal[p] > 0


# ---------------------------------------------------------------------------
# Interest
# ---------------------------------------------------------------------------

def test_interest_hedge_blending():
    """Hedged + unhedged should produce different interest from unhedged-only."""
    unhedged = _make_facility(hedge_pct=0.0, base_rate=0.04, margin=0.02)
    hedged = _make_facility(hedge_pct=0.75, swap_rate=0.03, base_rate=0.04, margin=0.02)
    out_u = calculate(_make_inputs(facilities=[unhedged]))
    out_h = calculate(_make_inputs(facilities=[hedged]))
    assert out_u.aggregate_total_interest != pytest.approx(out_h.aggregate_total_interest, abs=100)


def test_interest_on_declining_balance():
    """Interest should decrease as balance declines."""
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    # Compare interest at period 20 vs period 100
    assert r.interest[20] > r.interest[100]


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def test_arrangement_fee_at_first_drawdown():
    fac = _make_facility(arrangement_fee_pct=0.01)
    out = calculate(_make_inputs(facilities=[fac]))
    r = out.facility_results[0]
    assert r.arrangement_fee[0] == pytest.approx(100_000.0 * 0.01)
    assert sum(r.arrangement_fee) == pytest.approx(100_000.0 * 0.01)


# ---------------------------------------------------------------------------
# Balance mechanics
# ---------------------------------------------------------------------------

def test_balance_never_negative():
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    for p in range(180):
        assert r.closing_balance[p] >= -0.01


def test_opening_equals_prev_closing():
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    for p in range(1, 180):
        assert r.opening_balance[p] == pytest.approx(r.closing_balance[p - 1], abs=0.01)


def test_debt_service_equals_interest_plus_principal():
    out = calculate(_make_inputs())
    r = out.facility_results[0]
    for p in range(180):
        assert r.debt_service[p] == pytest.approx(
            r.interest[p] + r.principal[p], abs=0.01
        )


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_dict():
    refs = {
        "is_repayment_month": "TRUE", "facility": "$B$5",
        "n_repayments": "20", "opening": "C10",
        "hedge_pct": "$B$6", "swap_rate": "$B$7",
        "base_rate": "C20", "margin": "$B$8",
        "drawdown": "C11", "principal": "C12",
        "interest": "C13", "period": "C3",
        "first_draw_period": "0", "fee_pct": "$B$9",
    }
    formulas = get_excel_formulas(refs)
    assert isinstance(formulas, dict)
    for key in ("principal_linear", "interest_hedged", "interest_unhedged",
                "closing_balance", "debt_service", "arrangement_fee"):
        assert key in formulas
        assert formulas[key].startswith("=")
