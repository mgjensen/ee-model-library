"""Tests for DEBT_REFI_001 — sculpted refinancing facility."""

import math
import pytest
from modules.debt.DEBT_REFI_001 import (
    DSCRStream, MarginStep, Inputs, Outputs, calculate, get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _streams(n, refi_period=84):
    """4-stream CFADS: zero before refi, flat after."""
    zeros = [0.0] * refi_period
    ops = n - refi_period
    return [
        DSCRStream(name="pv_contracted", target_dscr=1.20,
                   cfads=zeros + [600.0] * ops),
        DSCRStream(name="pv_merchant", target_dscr=1.60,
                   cfads=zeros + [200.0] * ops),
        DSCRStream(name="bess_contracted", target_dscr=1.60,
                   cfads=zeros + [100.0] * ops),
        DSCRStream(name="bess_merchant", target_dscr=2.10,
                   cfads=zeros + [50.0] * ops),
    ]


def _make_inputs(
    periods=240,
    refi_period=84,        # 7 years into ops
    grace_periods=0,
    tenor_months=156,       # 13 years
    original_balance=80_000.0,
    base_rate=0.03,
    default_margin=0.015,
    margin_schedule=None,
    swap_rate=0.03068,
    hedge_pct=0.75,
    cash_sweep_active=False,
    cash_sweep_pct=0.0,
    arrangement_fee_pct=0.0125,
    **kwargs,
):
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        active=True,
        refi_period=refi_period,
        grace_periods=grace_periods,
        tenor_months=tenor_months,
        original_balance_at_refi_DKKk=original_balance,
        base_rate=base_rate,
        default_margin=default_margin,
        margin_schedule=margin_schedule or [],
        swap_rate=swap_rate,
        hedge_pct=hedge_pct,
        sculpted_dscr_streams=_streams(periods, refi_period),
        cash_sweep_active=cash_sweep_active,
        cash_sweep_pct=cash_sweep_pct,
        arrangement_fee_pct=arrangement_fee_pct,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Inactive / basic
# ---------------------------------------------------------------------------

def test_inactive_returns_zeros():
    inp = _make_inputs()
    inp = inp.model_copy(update={"active": False})
    out = calculate(inp)
    assert out.refi_facility_DKKk == 0.0
    assert all(v == 0.0 for v in out.opening_balance)
    assert out.fully_repaid is True


def test_output_is_outputs():
    out = calculate(_make_inputs())
    assert isinstance(out, Outputs)


def test_output_array_lengths():
    n = 240
    out = calculate(_make_inputs(periods=n))
    for field in ("opening_balance", "drawdown", "interest", "principal",
                   "cash_sweep", "closing_balance", "debt_service",
                   "arrangement_fee", "blended_dscr", "dscr_achieved", "llcr"):
        assert len(getattr(out, field)) == n, f"{field} length mismatch"


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def test_drawdown_equals_original_balance():
    out = calculate(_make_inputs(original_balance=80_000.0))
    assert out.refi_facility_DKKk == pytest.approx(80_000.0)
    assert out.drawdown[84] == pytest.approx(80_000.0)


def test_drawdown_in_correct_period():
    out = calculate(_make_inputs(refi_period=84))
    assert out.drawdown[84] > 0
    for p in range(240):
        if p != 84:
            assert out.drawdown[p] == 0.0


# ---------------------------------------------------------------------------
# Grace period
# ---------------------------------------------------------------------------

def test_no_repayment_during_grace():
    out = calculate(_make_inputs(grace_periods=6))
    # No principal during grace period (84..89)
    for p in range(84, 90):
        assert out.principal[p] == 0.0


# ---------------------------------------------------------------------------
# Balance mechanics
# ---------------------------------------------------------------------------

def test_balance_mechanics():
    """closing = opening + drawdown - principal - sweep."""
    out = calculate(_make_inputs())
    for p in range(240):
        expected = out.opening_balance[p] + out.drawdown[p] - out.principal[p] - out.cash_sweep[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=0.01)


def test_opening_equals_prev_closing():
    out = calculate(_make_inputs())
    for p in range(1, 240):
        assert out.opening_balance[p] == pytest.approx(out.closing_balance[p - 1], abs=0.01)


def test_balance_never_negative():
    out = calculate(_make_inputs())
    for p in range(240):
        assert out.closing_balance[p] >= -0.01


def test_fully_repaid_within_tenor():
    out = calculate(_make_inputs())
    assert out.fully_repaid is True


def test_total_principal_equals_facility():
    """Total principal + sweep should roughly equal facility if fully repaid."""
    out = calculate(_make_inputs())
    assert out.total_principal + out.total_cash_sweep == pytest.approx(
        out.refi_facility_DKKk, rel=0.01
    )


# ---------------------------------------------------------------------------
# Sculpted principal
# ---------------------------------------------------------------------------

def test_sculpted_principal_varies_with_cfads():
    """With varying CFADS, principal should not be constant."""
    n = 240
    rp = 84
    # Create non-uniform CFADS
    cfads_pv = [0.0] * rp + [400.0 + 5.0 * (p - rp) for p in range(rp, n)]
    streams = [
        DSCRStream(name="pv", target_dscr=1.30, cfads=cfads_pv),
    ]
    inp = _make_inputs()
    inp = inp.model_copy(update={"sculpted_dscr_streams": streams})
    out = calculate(inp)
    principals = [out.principal[p] for p in range(n) if out.principal[p] > 0]
    assert len(set(round(p, 2) for p in principals)) > 1, "Principal should vary"


# ---------------------------------------------------------------------------
# DSCR blending
# ---------------------------------------------------------------------------

def test_blended_dscr_4_streams():
    """Blended DSCR should be between min and max stream targets."""
    out = calculate(_make_inputs())
    for p in range(84, 240):
        if not math.isnan(out.blended_dscr[p]):
            assert 1.20 <= out.blended_dscr[p] <= 2.10


# ---------------------------------------------------------------------------
# Interest rate
# ---------------------------------------------------------------------------

def test_margin_step_up_applies():
    out1 = calculate(_make_inputs(default_margin=0.015))
    out2 = calculate(_make_inputs(
        margin_schedule=[
            MarginStep(margin=0.025, until_year=2033),
            MarginStep(margin=0.015, until_year=2060),
        ],
    ))
    # Higher early margin -> more total interest
    assert out2.total_interest > out1.total_interest


def test_hedge_blending():
    """Fully hedged should differ from fully unhedged."""
    out_hedged = calculate(_make_inputs(hedge_pct=1.0, swap_rate=0.02))
    out_unhedged = calculate(_make_inputs(hedge_pct=0.0))
    assert out_hedged.total_interest != pytest.approx(out_unhedged.total_interest, abs=100)


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def test_arrangement_fee_at_drawdown():
    out = calculate(_make_inputs(arrangement_fee_pct=0.0125))
    assert out.arrangement_fee[84] == pytest.approx(80_000.0 * 0.0125)
    assert sum(out.arrangement_fee) == pytest.approx(80_000.0 * 0.0125)


# ---------------------------------------------------------------------------
# Cash sweep
# ---------------------------------------------------------------------------

def test_cash_sweep_reduces_balance():
    base = calculate(_make_inputs())
    swept = calculate(_make_inputs(cash_sweep_active=True, cash_sweep_pct=0.20))
    mid = 150
    assert swept.closing_balance[mid] < base.closing_balance[mid]
    assert swept.total_cash_sweep > 0


# ---------------------------------------------------------------------------
# LLCR
# ---------------------------------------------------------------------------

def test_llcr_computed_correctly():
    out = calculate(_make_inputs())
    # At refi period, opening=0 so LLCR is NaN; check first period after drawdown
    llcr_vals = [out.llcr[p] for p in range(85, 240) if not math.isnan(out.llcr[p])]
    assert len(llcr_vals) > 0
    assert llcr_vals[0] > 1.0


def test_llcr_nan_before_refi():
    out = calculate(_make_inputs())
    for p in range(84):
        assert math.isnan(out.llcr[p])


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_dict():
    refs = {
        "period": "C3", "refi_period": "84", "original_balance": "$B$5",
        "opening_balance": "C62", "monthly_rate": "C90",
        "drawdown": "C63", "principal": "C65", "cash_sweep": "C66",
        "target_ds": "C80", "interest_per_repayment": "C81",
        "interest": "C64", "fee_pct": "$B$7", "pv_cfads": "C85",
    }
    formulas = get_excel_formulas(refs)
    assert isinstance(formulas, dict)
    for key in ("drawdown", "interest", "principal", "closing_balance",
                "debt_service", "arrangement_fee", "llcr"):
        assert key in formulas
        assert formulas[key].startswith("=")
