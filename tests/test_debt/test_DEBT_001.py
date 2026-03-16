"""Tests for DEBT_001 — senior debt schedule module."""

import math
import pytest
from modules.debt.DEBT_001 import (
    Inputs,
    Outputs,
    calculate,
    calculate_schedule,
    _infer_repayment_start,
    _annuity_payment,
    _year_groups,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _even_drawdowns(facility, n_draw, total_periods):
    """Spread facility evenly over n_draw periods; rest are zero."""
    d = [facility / n_draw] * n_draw + [0.0] * (total_periods - n_draw)
    return d


def _make_inputs(
    facility=100_000.0,
    rate=0.05,
    repayment_type="annuity",
    tenor_months=120,      # 10 years
    n_draw=12,             # 1 year construction
    periods=180,           # 15 years total
    start_year=2025,
    start_month=1,
    dscr_covenant=1.10,
    cfads=None,
    repayment_start_period=None,
):
    drawdowns = _even_drawdowns(facility, n_draw, periods)
    return Inputs(
        facility=facility,
        all_in_rate=rate,
        repayment_type=repayment_type,
        tenor_months=tenor_months,
        drawdowns=drawdowns,
        repayment_start_period=repayment_start_period,
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        dscr_covenant=dscr_covenant,
        cfads=cfads,
    )


# ---------------------------------------------------------------------------
# Unit: helpers
# ---------------------------------------------------------------------------

def test_infer_repayment_start_standard():
    draws = [1000.0, 2000.0, 0.0, 0.0, 0.0]
    assert _infer_repayment_start(draws) == 2


def test_infer_repayment_start_no_drawdowns():
    assert _infer_repayment_start([0.0, 0.0, 0.0]) == 0


def test_infer_repayment_start_last_period():
    draws = [0.0, 0.0, 500.0]
    assert _infer_repayment_start(draws) == 3  # beyond list → no repayment in model


def test_annuity_payment_zero_rate():
    """Zero rate → equal principal payments."""
    pmt = _annuity_payment(120_000.0, 0.0, 120)
    assert pmt == pytest.approx(1_000.0)


def test_annuity_payment_standard():
    """100,000 @ 5%/12 for 120 months — known value."""
    r = 0.05 / 12
    pmt = _annuity_payment(100_000.0, r, 120)
    # Standard formula: P × r / (1 − (1+r)^-n)
    expected = 100_000.0 * r / (1 - (1 + r) ** -120)
    assert pmt == pytest.approx(expected, rel=1e-9)


def test_annuity_payment_n_zero():
    """n=0 → return balance immediately."""
    assert _annuity_payment(50_000.0, 0.05 / 12, 0) == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# Unit: balance mechanics
# ---------------------------------------------------------------------------

def test_opening_balance_starts_zero():
    out = calculate(_make_inputs())
    assert out.opening_balance[0] == pytest.approx(0.0)


def test_closing_equals_opening_plus_drawdown_minus_principal():
    out = calculate(_make_inputs())
    for p in range(len(out.closing_balance)):
        expected = out.opening_balance[p] + out.drawdown[p] - out.principal[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


def test_opening_equals_previous_closing():
    out = calculate(_make_inputs())
    for p in range(1, len(out.opening_balance)):
        assert out.opening_balance[p] == pytest.approx(out.closing_balance[p - 1], abs=1e-6)


def test_interest_on_opening_balance():
    out = calculate(_make_inputs(rate=0.06))
    r = 0.06 / 12
    for p in range(len(out.interest)):
        assert out.interest[p] == pytest.approx(out.opening_balance[p] * r, abs=1e-6)


def test_balance_increases_during_drawdown():
    out = calculate(_make_inputs(n_draw=12, periods=180))
    # After 12 drawdowns of equal size, balance = facility
    assert out.closing_balance[11] == pytest.approx(100_000.0, rel=1e-6)


def test_no_principal_during_drawdown():
    out = calculate(_make_inputs(n_draw=12, periods=180))
    for p in range(12):
        assert out.principal[p] == pytest.approx(0.0)


def test_debt_service_equals_interest_plus_principal():
    out = calculate(_make_inputs())
    for p in range(len(out.debt_service)):
        assert out.debt_service[p] == pytest.approx(
            out.interest[p] + out.principal[p], abs=1e-9
        )


# ---------------------------------------------------------------------------
# Unit: annuity repayment
# ---------------------------------------------------------------------------

def test_annuity_total_payment_constant_during_repayment():
    """After repayment starts, total payment (interest + principal) is constant."""
    out = calculate(_make_inputs(repayment_type="annuity", n_draw=6, tenor_months=60, periods=72))
    rep_start = 6
    payments = [out.debt_service[p] for p in range(rep_start, len(out.debt_service))
                if out.closing_balance[p] > 0.01]
    if len(payments) > 1:
        assert max(payments) - min(payments) == pytest.approx(0.0, abs=1e-4)


def test_annuity_balance_reaches_zero():
    """Annuity should fully repay within tenor."""
    out = calculate(_make_inputs(
        facility=100_000.0, rate=0.05, repayment_type="annuity",
        tenor_months=120, n_draw=12, periods=132,
    ))
    assert out.fully_repaid
    assert out.final_balance == pytest.approx(0.0, abs=1.0)  # DKKk tolerance


def test_annuity_total_principal_equals_facility():
    out = calculate(_make_inputs(
        facility=100_000.0, rate=0.05, repayment_type="annuity",
        tenor_months=120, n_draw=12, periods=132,
    ))
    assert out.total_principal == pytest.approx(100_000.0, abs=1.0)


# ---------------------------------------------------------------------------
# Unit: straight-line repayment
# ---------------------------------------------------------------------------

def test_straight_line_constant_principal():
    """All principal payments during repayment are equal."""
    out = calculate(_make_inputs(
        repayment_type="straight_line", n_draw=12, tenor_months=120, periods=132
    ))
    rep_principals = [out.principal[p] for p in range(12, 132) if out.opening_balance[p] > 0.01]
    assert max(rep_principals) - min(rep_principals) == pytest.approx(0.0, abs=1e-4)


def test_straight_line_fully_repaid():
    out = calculate(_make_inputs(
        facility=120_000.0, rate=0.05, repayment_type="straight_line",
        tenor_months=120, n_draw=12, periods=132
    ))
    assert out.fully_repaid
    assert out.total_principal == pytest.approx(120_000.0, abs=1.0)


def test_straight_line_decreasing_debt_service():
    """SL debt service decreases over time (lower interest as balance falls)."""
    out = calculate(_make_inputs(
        repayment_type="straight_line", n_draw=6, tenor_months=60, periods=66
    ))
    ds = [out.debt_service[p] for p in range(6, 66)]
    # Should be monotonically non-increasing
    for i in range(len(ds) - 1):
        assert ds[i] >= ds[i + 1] - 1e-6


# ---------------------------------------------------------------------------
# Unit: bullet repayment
# ---------------------------------------------------------------------------

def test_bullet_no_principal_before_maturity():
    out = calculate(_make_inputs(
        repayment_type="bullet", n_draw=6, tenor_months=24, periods=36
    ))
    # Repayment starts period 6; bullet fires at period 6+24-1 = 29
    for p in range(6, 29):
        assert out.principal[p] == pytest.approx(0.0, abs=1e-9)


def test_bullet_principal_at_maturity():
    out = calculate(_make_inputs(
        facility=50_000.0, repayment_type="bullet",
        n_draw=6, tenor_months=24, periods=36
    ))
    # Bullet period = repayment_start + tenor - 1 = 6 + 24 - 1 = 29
    assert out.principal[29] == pytest.approx(50_000.0, abs=0.1)


def test_bullet_balance_flat_during_interest_only():
    out = calculate(_make_inputs(
        facility=50_000.0, repayment_type="bullet",
        n_draw=6, tenor_months=24, periods=36
    ))
    # Between period 6 and 28 (before bullet), balance should be flat at 50,000
    for p in range(6, 29):
        assert out.closing_balance[p] == pytest.approx(50_000.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Unit: DSCR
# ---------------------------------------------------------------------------

def _cfads_at_ratio(out: Outputs, ratio: float) -> list[float]:
    """Generate CFADS = ratio × debt_service for each period."""
    return [ds * ratio for ds in out.debt_service]


def test_dscr_nan_when_no_cfads():
    out = calculate(_make_inputs(cfads=None))
    assert all(math.isnan(v) for v in out.dscr_annual)
    assert math.isnan(out.min_dscr)


def test_dscr_computed_during_repayment():
    base = calculate(_make_inputs(n_draw=12, tenor_months=60, periods=72))
    cfads = _cfads_at_ratio(base, 1.5)
    inp = _make_inputs(n_draw=12, tenor_months=60, periods=72, cfads=cfads)
    out = calculate(inp)
    # DSCR should be ~1.5 during repayment years
    rep_dscrs = [v for v in out.dscr_annual[12:] if not math.isnan(v)]
    assert all(abs(v - 1.5) < 0.01 for v in rep_dscrs)


def test_dscr_no_covenant_breach_above_threshold():
    base = calculate(_make_inputs(n_draw=12, tenor_months=60, periods=72))
    cfads = _cfads_at_ratio(base, 1.5)
    inp = _make_inputs(n_draw=12, tenor_months=60, periods=72,
                       cfads=cfads, dscr_covenant=1.10)
    out = calculate(inp)
    assert not out.covenant_breached
    assert out.min_dscr == pytest.approx(1.5, rel=0.01)


def test_dscr_covenant_breach_below_threshold():
    base = calculate(_make_inputs(n_draw=12, tenor_months=60, periods=72))
    cfads = _cfads_at_ratio(base, 0.8)  # DSCR = 0.80 < 1.10 covenant
    inp = _make_inputs(n_draw=12, tenor_months=60, periods=72,
                       cfads=cfads, dscr_covenant=1.10)
    out = calculate(inp)
    assert out.covenant_breached
    assert out.min_dscr < 1.10


def test_dscr_nan_during_construction():
    """No debt service during construction → DSCR is nan."""
    base = calculate(_make_inputs(n_draw=12, tenor_months=60, periods=72))
    cfads = _cfads_at_ratio(base, 1.5)
    inp = _make_inputs(n_draw=12, tenor_months=60, periods=72, cfads=cfads)
    out = calculate(inp)
    # First 12 periods are construction — no debt service → dscr_annual is nan
    assert all(math.isnan(out.dscr_annual[p]) for p in range(12))


# ---------------------------------------------------------------------------
# Integration: Project Viuf proxy
# ---------------------------------------------------------------------------

def test_viuf_proxy_annuity():
    """
    Proxy for Project Viuf: 450,000 DKKk, 25y repayment, 5.0% all-in,
    24-month construction (drawdown spread evenly).
    Validate: fully repaid, total principal = facility.
    """
    periods = 24 + 300  # 2y construction + 25y repayment
    drawdowns = _even_drawdowns(450_000.0, 24, periods)
    inp = Inputs(
        facility=450_000.0,
        all_in_rate=0.05,
        repayment_type="annuity",
        tenor_months=300,
        drawdowns=drawdowns,
        periods=periods,
        start_year=2026,
        start_month=1,
    )
    out = calculate(inp)
    assert out.fully_repaid
    assert out.total_principal == pytest.approx(450_000.0, abs=1.0)
    assert out.total_interest > 0
    # Total interest on a 450m 5% 25y annuity should be substantial
    assert out.total_interest > 100_000.0


def test_viuf_proxy_repayment_end_period():
    """Balance should be ~0 at period 24+300-1 = 323."""
    periods = 324
    drawdowns = _even_drawdowns(450_000.0, 24, periods)
    inp = Inputs(
        facility=450_000.0,
        all_in_rate=0.05,
        repayment_type="annuity",
        tenor_months=300,
        drawdowns=drawdowns,
        periods=periods,
        start_year=2026,
        start_month=1,
    )
    out = calculate(inp)
    assert out.closing_balance[323] == pytest.approx(0.0, abs=1.0)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_total_interest_positive():
    out = calculate(_make_inputs(rate=0.05))
    assert out.total_interest > 0


def test_total_principal_equals_facility():
    out = calculate(_make_inputs(
        facility=50_000.0, repayment_type="annuity",
        tenor_months=60, n_draw=6, periods=66
    ))
    assert out.total_principal == pytest.approx(50_000.0, abs=0.5)


def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)


def test_principal_never_negative():
    out = calculate(_make_inputs())
    assert all(p >= -1e-9 for p in out.principal)


def test_explicit_repayment_start():
    """Explicit repayment_start_period overrides inference."""
    out = calculate(_make_inputs(n_draw=6, repayment_start_period=12))
    for p in range(12):
        assert out.principal[p] == pytest.approx(0.0)
    # Period 12 should have non-zero principal
    assert out.principal[12] > 0


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "closing_prev": "K9", "opening_bal": "K10", "drawdown": "K11",
        "monthly_rate": "B5", "annuity_pmt": "G12", "interest": "K13",
        "principal": "K14", "sl_principal_fixed": "G13",
        "debt_service": "K15", "cfads_range": "K$20:K$20",
        "ds_range": "K$15:K$15", "year_range": "K$3:K$3",
        "year_ref": "K3", "balance": "G10", "tenor_months": "B6",
    }
    formulas = get_excel_formulas(refs)
    for key in ("opening_balance", "interest", "annuity_principal", "closing_balance",
                "debt_service", "annuity_payment"):
        assert key in formulas
        assert formulas[key].startswith("=")
