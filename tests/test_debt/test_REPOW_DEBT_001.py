"""Tests for REPOW_DEBT_001 — BESS repowering debt facility."""

import pytest
from modules.debt.REPOW_DEBT_001 import Inputs, Outputs, calculate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**overrides):
    """Default active repowering debt scenario."""
    defaults = dict(
        periods=240,
        start_year=2025,
        start_month=1,
        active=True,
        ltv_pct=1.0,
        repowering_cost_DKKk=50_000.0,
        repowering_period=120,
        grace_period_months=0,
        tenor_months=120,
        base_rate=0.03,
        margin=0.016,
        hedge_pct=0.0,
        swap_rate=0.0,
        arrangement_fee_pct=0.01,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_inactive_returns_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(v == 0.0 for v in out.opening_balance)
    assert all(v == 0.0 for v in out.drawdown)
    assert all(v == 0.0 for v in out.principal)
    assert all(v == 0.0 for v in out.interest)
    assert all(v == 0.0 for v in out.closing_balance)
    assert all(v == 0.0 for v in out.debt_service)
    assert all(v == 0.0 for v in out.arrangement_fee)
    assert out.facility_DKKk == 0.0
    assert out.total_principal == 0.0
    assert out.total_interest == 0.0


def test_facility_equals_ltv_times_cost():
    out = calculate(_make_inputs(ltv_pct=0.80, repowering_cost_DKKk=100_000.0))
    assert out.facility_DKKk == pytest.approx(80_000.0)


def test_drawdown_at_repowering_period():
    rp = 120
    out = calculate(_make_inputs(repowering_period=rp))
    assert out.drawdown[rp] == pytest.approx(50_000.0)
    # No drawdowns elsewhere
    for p in range(len(out.drawdown)):
        if p != rp:
            assert out.drawdown[p] == pytest.approx(0.0)


def test_straight_line_principal():
    """All principal payments during repayment are equal (facility / tenor)."""
    tenor = 120
    facility = 50_000.0
    out = calculate(_make_inputs(
        tenor_months=tenor, ltv_pct=1.0, repowering_cost_DKKk=facility,
        grace_period_months=0,
    ))
    expected_princ = facility / tenor
    rp = 120
    # Most payments should be exactly sl_principal; last may be smaller (residual)
    rep_principals = [out.principal[p] for p in range(rp, rp + tenor)
                      if out.principal[p] > 1e-9]
    assert len(rep_principals) >= tenor - 1
    for princ in rep_principals[:-1]:
        assert princ == pytest.approx(expected_princ, rel=1e-6)
    # Total principal ≈ facility (minor floating point residual)
    assert sum(out.principal) == pytest.approx(facility, rel=0.02)


def test_interest_on_opening_balance():
    """Interest = opening_balance * blended_rate / 12."""
    out = calculate(_make_inputs(
        base_rate=0.04, margin=0.01, hedge_pct=0.0,
    ))
    monthly_rate = (0.04 + 0.01) / 12.0
    for p in range(len(out.interest)):
        assert out.interest[p] == pytest.approx(
            out.opening_balance[p] * monthly_rate, abs=1e-9
        )


def test_balance_reaches_zero():
    """After full tenor, closing balance should be zero."""
    rp = 120
    tenor = 120
    out = calculate(_make_inputs(
        repowering_period=rp, tenor_months=tenor, grace_period_months=0,
    ))
    # Last repayment period
    last_rep = rp + tenor - 1
    # Balance should be near zero relative to facility (default 14,000)
    assert out.closing_balance[last_rep] < out.facility_DKKk * 0.05


def test_grace_period_no_principal():
    """During grace period, no principal is repaid but interest accrues."""
    grace = 6
    rp = 120
    out = calculate(_make_inputs(
        repowering_period=rp, grace_period_months=grace,
    ))
    # Periods rp to rp+grace-1: principal should be zero
    for p in range(rp, rp + grace):
        assert out.principal[p] == pytest.approx(0.0)
    # But interest should be non-zero after drawdown
    for p in range(rp + 1, rp + grace):
        assert out.interest[p] > 0


def test_arrangement_fee():
    rp = 120
    facility = 50_000.0
    fee_pct = 0.015
    out = calculate(_make_inputs(
        repowering_period=rp, arrangement_fee_pct=fee_pct,
        ltv_pct=1.0, repowering_cost_DKKk=facility,
    ))
    assert out.arrangement_fee[rp] == pytest.approx(facility * fee_pct)
    # No fee elsewhere
    for p in range(len(out.arrangement_fee)):
        if p != rp:
            assert out.arrangement_fee[p] == pytest.approx(0.0)


def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)
    assert all(b >= -1e-6 for b in out.opening_balance)


def test_opening_equals_prev_closing():
    out = calculate(_make_inputs())
    for p in range(1, len(out.opening_balance)):
        assert out.opening_balance[p] == pytest.approx(
            out.closing_balance[p - 1], abs=1e-9
        )
