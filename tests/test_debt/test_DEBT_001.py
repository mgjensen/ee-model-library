"""Tests for DEBT_001 — senior debt schedule module."""

import math
import pytest
from modules.debt.DEBT_001 import (
    DSCRStream,
    Inputs,
    Outputs,
    calculate,
    calculate_schedule,
    _infer_repayment_start,
    _annuity_payment,
    _year_groups,
    _blended_dscr_by_year,
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
        "blended_dscr": "G20", "interest_range": "K$13:K$13",
        "ops_flag_range": "K$4:K$4",
    }
    formulas = get_excel_formulas(refs)
    for key in ("opening_balance", "interest", "annuity_principal", "closing_balance",
                "debt_service", "annuity_payment", "sculpted_principal"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# Non-sculpted: new output fields
# ---------------------------------------------------------------------------

def test_non_sculpted_sized_facility_equals_facility():
    out = calculate(_make_inputs(facility=100_000.0))
    assert out.sized_facility == pytest.approx(100_000.0)


def test_non_sculpted_blended_dscr_is_nan():
    out = calculate(_make_inputs())
    assert all(math.isnan(v) for v in out.blended_dscr_annual)


# ---------------------------------------------------------------------------
# Sculpted: helpers
# ---------------------------------------------------------------------------

def _sculpted_streams(periods, n_draw, pv_cfads_monthly=800.0, bess_cfads_monthly=200.0):
    """Create two CFADS streams: PV contracted + BESS merchant."""
    zeros = [0.0] * n_draw
    ops = periods - n_draw
    return [
        DSCRStream(
            name="pv_contracted",
            target_dscr=1.20,
            cfads=zeros + [pv_cfads_monthly] * ops,
        ),
        DSCRStream(
            name="bess_merchant",
            target_dscr=1.80,
            cfads=zeros + [bess_cfads_monthly] * ops,
        ),
    ]


def _make_sculpted_inputs(
    facility=100_000.0,
    rate=0.05,
    tenor_months=120,
    n_draw=12,
    periods=180,
    pv_cfads=800.0,
    bess_cfads=200.0,
    leverage_cap_pct=None,
    total_capex_DKKk=None,
):
    drawdowns = _even_drawdowns(facility, n_draw, periods)
    streams = _sculpted_streams(periods, n_draw, pv_cfads, bess_cfads)
    return Inputs(
        facility=facility,
        all_in_rate=rate,
        repayment_type="sculpted",
        tenor_months=tenor_months,
        drawdowns=drawdowns,
        periods=periods,
        start_year=2025,
        start_month=1,
        sculpted_dscr_streams=streams,
        leverage_cap_pct=leverage_cap_pct,
        total_capex_DKKk=total_capex_DKKk,
    )


# ---------------------------------------------------------------------------
# Sculpted: validation
# ---------------------------------------------------------------------------

def test_sculpted_requires_streams():
    with pytest.raises(ValueError, match="sculpted_dscr_streams"):
        Inputs(
            facility=100_000.0, all_in_rate=0.05,
            repayment_type="sculpted", tenor_months=120,
            drawdowns=_even_drawdowns(100_000.0, 12, 180),
            periods=180, start_year=2025, start_month=1,
        )


def test_sculpted_validates_stream_cfads_length():
    with pytest.raises(ValueError, match="sculpted_dscr_streams"):
        Inputs(
            facility=100_000.0, all_in_rate=0.05,
            repayment_type="sculpted", tenor_months=120,
            drawdowns=_even_drawdowns(100_000.0, 12, 180),
            periods=180, start_year=2025, start_month=1,
            sculpted_dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.2, cfads=[100.0] * 10),  # wrong length
            ],
        )


def test_sculpted_leverage_cap_requires_capex():
    with pytest.raises(ValueError, match="total_capex_DKKk"):
        Inputs(
            facility=100_000.0, all_in_rate=0.05,
            repayment_type="sculpted", tenor_months=120,
            drawdowns=_even_drawdowns(100_000.0, 12, 180),
            periods=180, start_year=2025, start_month=1,
            sculpted_dscr_streams=_sculpted_streams(180, 12),
            leverage_cap_pct=0.70,
        )


# ---------------------------------------------------------------------------
# Sculpted: blended DSCR
# ---------------------------------------------------------------------------

def test_blended_dscr_single_stream():
    """One stream → blended = stream target."""
    n = 24
    yg = _year_groups(n, 2025, 1)
    streams = [DSCRStream(name="pv", target_dscr=1.30, cfads=[100.0] * n)]
    blended = _blended_dscr_by_year(streams, yg)
    for v in blended.values():
        assert v == pytest.approx(1.30)


def test_blended_dscr_two_streams_weighted():
    """80% PV (1.2×) + 20% BESS (1.8×) → blended = 1.32×."""
    n = 12
    yg = _year_groups(n, 2025, 1)
    streams = [
        DSCRStream(name="pv", target_dscr=1.20, cfads=[800.0] * n),
        DSCRStream(name="bess", target_dscr=1.80, cfads=[200.0] * n),
    ]
    blended = _blended_dscr_by_year(streams, yg)
    # blended = (800*1.2 + 200*1.8) / (800+200) = (960+360)/1000 = 1.32
    assert blended[2025] == pytest.approx(1.32)


def test_blended_dscr_zero_cfads_year():
    """Year with zero CFADS → blended defaults to 999."""
    n = 24
    yg = _year_groups(n, 2025, 1)
    streams = [
        DSCRStream(name="pv", target_dscr=1.20, cfads=[0.0] * 12 + [100.0] * 12),
    ]
    blended = _blended_dscr_by_year(streams, yg)
    assert blended[2025] == pytest.approx(999.0)
    assert blended[2026] == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# Sculpted: balance mechanics
# ---------------------------------------------------------------------------

def test_sculpted_returns_outputs():
    out = calculate(_make_sculpted_inputs())
    assert isinstance(out, Outputs)


def test_sculpted_array_lengths():
    n = 180
    out = calculate(_make_sculpted_inputs(periods=n))
    for field in ("opening_balance", "drawdown", "interest", "principal",
                  "closing_balance", "debt_service", "dscr_annual",
                  "blended_dscr_annual"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


def test_sculpted_closing_equals_opening_plus_drawdown_minus_principal():
    out = calculate(_make_sculpted_inputs())
    for p in range(len(out.closing_balance)):
        expected = out.opening_balance[p] + out.drawdown[p] - out.principal[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


def test_sculpted_opening_equals_previous_closing():
    out = calculate(_make_sculpted_inputs())
    for p in range(1, len(out.opening_balance)):
        assert out.opening_balance[p] == pytest.approx(out.closing_balance[p - 1], abs=1e-6)


def test_sculpted_no_principal_during_construction():
    n_draw = 12
    out = calculate(_make_sculpted_inputs(n_draw=n_draw))
    for p in range(n_draw):
        assert out.principal[p] == pytest.approx(0.0)


def test_sculpted_balance_never_negative():
    out = calculate(_make_sculpted_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)


def test_sculpted_principal_never_negative():
    out = calculate(_make_sculpted_inputs())
    assert all(p >= -1e-9 for p in out.principal)


def test_sculpted_debt_service_equals_interest_plus_principal():
    out = calculate(_make_sculpted_inputs())
    for p in range(len(out.debt_service)):
        assert out.debt_service[p] == pytest.approx(
            out.interest[p] + out.principal[p], abs=1e-9
        )


# ---------------------------------------------------------------------------
# Sculpted: DSCR targeting
# ---------------------------------------------------------------------------

def test_sculpted_achieved_dscr_meets_blended_target():
    """Annual DSCR during repayment should be >= blended target."""
    out = calculate(_make_sculpted_inputs(
        facility=100_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=800.0, bess_cfads=200.0,
    ))
    # blended target = 1.32
    rep_dscrs = [v for v in out.dscr_annual[12:] if not math.isnan(v)]
    # Achieved DSCR should be >= target (sculpting is slightly conservative)
    assert all(d >= 1.30 for d in rep_dscrs)


def test_sculpted_blended_dscr_annual_populated():
    out = calculate(_make_sculpted_inputs())
    # During operations, blended_dscr_annual should not be nan
    assert not math.isnan(out.blended_dscr_annual[12])
    assert out.blended_dscr_annual[12] == pytest.approx(1.32)


def test_sculpted_principal_varies_with_cfads():
    """Higher CFADS → higher principal (sculpted adjusts to cash flow)."""
    out_low = calculate(_make_sculpted_inputs(pv_cfads=500.0, bess_cfads=100.0))
    out_high = calculate(_make_sculpted_inputs(pv_cfads=2000.0, bess_cfads=500.0))
    # Year 2 (periods 12-23) principal should be higher with more CFADS
    low_yr2 = sum(out_low.principal[p] for p in range(12, 24))
    high_yr2 = sum(out_high.principal[p] for p in range(12, 24))
    assert high_yr2 > low_yr2


# ---------------------------------------------------------------------------
# Sculpted: auto-sizing
# ---------------------------------------------------------------------------

def test_sculpted_auto_sizes_below_facility():
    """With tight CFADS, sized facility should be less than nominal facility."""
    out = calculate(_make_sculpted_inputs(
        facility=500_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=400.0, bess_cfads=100.0,
    ))
    assert out.sized_facility < 500_000.0
    assert out.sized_facility > 0.0
    assert out.fully_repaid


def test_sculpted_auto_sizes_to_full_when_cfads_abundant():
    """With abundant CFADS, sized facility should equal nominal facility."""
    out = calculate(_make_sculpted_inputs(
        facility=50_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=5000.0, bess_cfads=2000.0,
    ))
    assert out.sized_facility == pytest.approx(50_000.0, rel=1e-4)


def test_sculpted_fully_repaid_within_tenor():
    """Auto-sized sculpted debt should be fully repaid."""
    out = calculate(_make_sculpted_inputs(
        facility=200_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132,
    ))
    assert out.fully_repaid
    assert out.final_balance == pytest.approx(0.0, abs=1.0)


def test_sculpted_total_principal_equals_sized_facility():
    out = calculate(_make_sculpted_inputs(
        facility=200_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132,
    ))
    assert out.total_principal == pytest.approx(out.sized_facility, abs=1.0)


# ---------------------------------------------------------------------------
# Sculpted: leverage cap
# ---------------------------------------------------------------------------

def test_sculpted_leverage_cap_binds():
    """Leverage cap should limit facility below DSCR-sized amount."""
    # Large CFADS → DSCR sizing alone would support full facility
    out_uncapped = calculate(_make_sculpted_inputs(
        facility=200_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=5000.0, bess_cfads=2000.0,
    ))
    out_capped = calculate(_make_sculpted_inputs(
        facility=200_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=5000.0, bess_cfads=2000.0,
        leverage_cap_pct=0.50, total_capex_DKKk=200_000.0,
    ))
    assert out_uncapped.sized_facility == pytest.approx(200_000.0, rel=1e-4)
    assert out_capped.sized_facility == pytest.approx(100_000.0, rel=1e-4)


def test_sculpted_leverage_cap_does_not_bind_when_dscr_tighter():
    """When DSCR sizing < leverage cap, leverage cap doesn't bind."""
    out = calculate(_make_sculpted_inputs(
        facility=500_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=400.0, bess_cfads=100.0,
        leverage_cap_pct=0.90, total_capex_DKKk=500_000.0,
    ))
    # DSCR-sized should be < 450,000 (= 0.9 × 500,000)
    assert out.sized_facility < 450_000.0


def test_sculpted_drawdowns_scaled_with_facility():
    """When auto-sizing reduces facility, drawdowns should scale proportionally."""
    out = calculate(_make_sculpted_inputs(
        facility=200_000.0, rate=0.05, tenor_months=120,
        n_draw=12, periods=132, pv_cfads=5000.0, bess_cfads=2000.0,
        leverage_cap_pct=0.50, total_capex_DKKk=200_000.0,
    ))
    # Sized facility = 100,000, so drawdowns should sum to ~100,000
    assert sum(out.drawdown) == pytest.approx(out.sized_facility, abs=1.0)


# ---------------------------------------------------------------------------
# Sculpted: 4-stream scenario (PV contracted, PV merchant, BESS contracted, BESS merchant)
# ---------------------------------------------------------------------------

def test_sculpted_four_streams():
    """Full 4-stream scenario matching spec requirements."""
    n = 132
    n_draw = 12
    ops = n - n_draw
    zeros = [0.0] * n_draw

    streams = [
        DSCRStream(name="pv_contracted", target_dscr=1.20, cfads=zeros + [400.0] * ops),
        DSCRStream(name="pv_merchant", target_dscr=1.50, cfads=zeros + [200.0] * ops),
        DSCRStream(name="bess_contracted", target_dscr=1.20, cfads=zeros + [100.0] * ops),
        DSCRStream(name="bess_merchant", target_dscr=1.80, cfads=zeros + [100.0] * ops),
    ]
    # blended = (400*1.2 + 200*1.5 + 100*1.2 + 100*1.8) / 800
    #         = (480 + 300 + 120 + 180) / 800 = 1080/800 = 1.35
    inp = Inputs(
        facility=200_000.0,
        all_in_rate=0.05,
        repayment_type="sculpted",
        tenor_months=120,
        drawdowns=_even_drawdowns(200_000.0, n_draw, n),
        periods=n,
        start_year=2025,
        start_month=1,
        sculpted_dscr_streams=streams,
    )
    out = calculate(inp)

    assert isinstance(out, Outputs)
    assert out.fully_repaid
    assert out.blended_dscr_annual[12] == pytest.approx(1.35)
    assert out.sized_facility > 0
    assert out.total_principal == pytest.approx(out.sized_facility, abs=1.0)


# ---------------------------------------------------------------------------
# Dual DSCR covenant tests
# ---------------------------------------------------------------------------

class TestDualDSCR:
    def _make_dual(self, **kwargs):
        n = 180
        defaults = dict(
            facility=100_000.0, all_in_rate=0.05, repayment_type="straight_line",
            tenor_months=120, drawdowns=_even_drawdowns(100_000, 12, n),
            periods=n, start_year=2025, start_month=1,
            cfads=[0.0] * 12 + [2000.0] * 168,
        )
        defaults.update(kwargs)
        return Inputs(**defaults)

    def test_dual_dscr_none_falls_back_to_single(self):
        out = calculate(self._make_dual())
        assert len(out.dscr_covenant_applicable) == 180
        assert all(v == 1.10 for v in out.dscr_covenant_applicable)
        assert out.covenant_breached_ppa is False
        assert out.covenant_breached_merchant is False

    def test_dual_dscr_ppa_period_applies_lower_covenant(self):
        out = calculate(self._make_dual(
            dscr_covenant_ppa=1.05, dscr_covenant_merchant=1.30,
            ppa_start_period=12, ppa_end_period=71,
        ))
        assert out.dscr_covenant_applicable[12] == 1.05
        assert out.dscr_covenant_applicable[50] == 1.05

    def test_dual_dscr_merchant_period_applies_higher_covenant(self):
        out = calculate(self._make_dual(
            dscr_covenant_ppa=1.05, dscr_covenant_merchant=1.30,
            ppa_start_period=12, ppa_end_period=71,
        ))
        assert out.dscr_covenant_applicable[0] == 1.30
        assert out.dscr_covenant_applicable[72] == 1.30

    def test_dual_dscr_no_breach_either(self):
        out = calculate(self._make_dual(
            dscr_covenant_ppa=1.05, dscr_covenant_merchant=1.10,
            ppa_start_period=12, ppa_end_period=131,
            cfads=[0.0] * 12 + [5000.0] * 168,
        ))
        assert out.covenant_breached_ppa is False
        assert out.covenant_breached_merchant is False

    def test_dual_dscr_breach_merchant_only(self):
        # PPA covers first 3 years of ops (12-47), merchant from 48 onward
        out = calculate(self._make_dual(
            dscr_covenant_ppa=0.50, dscr_covenant_merchant=99.0,
            ppa_start_period=12, ppa_end_period=47,
        ))
        assert out.covenant_breached_merchant is True

    def test_dual_dscr_covenant_applicable_length(self):
        out = calculate(self._make_dual(
            dscr_covenant_ppa=1.05, dscr_covenant_merchant=1.30,
            ppa_start_period=12, ppa_end_period=71,
        ))
        assert len(out.dscr_covenant_applicable) == 180
