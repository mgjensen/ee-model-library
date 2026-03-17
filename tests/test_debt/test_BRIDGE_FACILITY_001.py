"""Tests for BRIDGE_FACILITY_001 — short-term bridge loan + recap."""

import pytest
from modules.debt.BRIDGE_FACILITY_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(**overrides):
    defaults = dict(
        periods=24,
        start_year=2025,
        start_month=1,
        active=True,
        facility_size=50_000.0,
        drawdown_period=0,
        repayment_period=6,
        margin=0.0175,
        base_rate=0.03,
        maturity_period=6,
        extension_fee_daily=0.0,
        recap_active=False,
        recap_amount=0.0,
        recap_margin=0.089,
        recap_base_rate=0.03,
        recap_repayment_period=0,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_drawdown_period_exceeds_periods():
    with pytest.raises(ValueError, match="drawdown_period"):
        _make_inputs(periods=12, drawdown_period=12)


def test_repayment_before_drawdown():
    with pytest.raises(ValueError, match="repayment_period"):
        _make_inputs(drawdown_period=6, repayment_period=3)


def test_repayment_exceeds_periods():
    with pytest.raises(ValueError, match="repayment_period"):
        _make_inputs(periods=12, repayment_period=12)


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 24
    out = calculate(_make_inputs(periods=n))
    for field in ("bridge_opening", "bridge_closing", "bridge_drawdown",
                  "bridge_repayment", "bridge_interest", "extension_fees",
                  "recap_opening", "recap_closing", "recap_interest",
                  "recap_repayment", "total_short_term_interest",
                  "total_short_term_repayment", "total_short_term_balance"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Bridge mechanics
# ---------------------------------------------------------------------------

def test_drawdown_at_correct_period():
    out = calculate(_make_inputs(drawdown_period=3))
    assert out.bridge_drawdown[3] == pytest.approx(50_000.0)
    assert out.bridge_drawdown[0] == pytest.approx(0.0)


def test_bullet_repayment_at_correct_period():
    out = calculate(_make_inputs(drawdown_period=0, repayment_period=6))
    assert out.bridge_repayment[6] == pytest.approx(50_000.0)
    assert out.bridge_closing[6] == pytest.approx(0.0)


def test_bridge_interest_calculation():
    inp = _make_inputs(margin=0.02, base_rate=0.04)
    out = calculate(inp)
    monthly_rate = (0.04 + 0.02) / 12.0
    # Period 0: drawn, interest on 50_000
    assert out.bridge_interest[0] == pytest.approx(50_000.0 * monthly_rate)


def test_bridge_closing_zero_after_repayment():
    out = calculate(_make_inputs(repayment_period=3))
    for p in range(4, 24):
        assert out.bridge_closing[p] == pytest.approx(0.0)


def test_bridge_interest_zero_after_repayment():
    out = calculate(_make_inputs(repayment_period=3))
    for p in range(4, 24):
        assert out.bridge_interest[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Extension fees
# ---------------------------------------------------------------------------

def test_no_extension_fee_before_maturity():
    out = calculate(_make_inputs(
        maturity_period=6, repayment_period=10,
        extension_fee_daily=100.0,
    ))
    for p in range(7):
        assert out.extension_fees[p] == pytest.approx(0.0)


def test_extension_fee_after_maturity():
    out = calculate(_make_inputs(
        maturity_period=3, repayment_period=10,
        extension_fee_daily=100.0,
    ))
    # Period 4: past maturity, bridge still outstanding
    assert out.extension_fees[4] == pytest.approx(100.0 * 30.0)


# ---------------------------------------------------------------------------
# Inactive
# ---------------------------------------------------------------------------

def test_inactive_all_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(v == 0.0 for v in out.bridge_interest)
    assert all(v == 0.0 for v in out.bridge_drawdown)
    assert out.total_bridge_interest == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Recap facility
# ---------------------------------------------------------------------------

def test_recap_interest():
    out = calculate(_make_inputs(
        recap_active=True, recap_amount=10_000.0,
        recap_margin=0.10, recap_base_rate=0.02,
        recap_repayment_period=12,
    ))
    monthly_rate = (0.02 + 0.10) / 12.0
    assert out.recap_interest[0] == pytest.approx(10_000.0 * monthly_rate)
    assert out.total_recap_interest > 0


def test_recap_inactive_zeros():
    out = calculate(_make_inputs(recap_active=False, recap_amount=10_000.0))
    assert all(v == 0.0 for v in out.recap_interest)


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "prev_closing": "K10", "drawdown": "K11", "bridge_opening": "K12",
        "base_rate": "B5", "margin": "B6", "period": "K1",
        "repayment_period": "B7", "bridge_repayment": "K14",
        "bridge_closing": "K15", "maturity_period": "B8",
        "ext_fee_daily": "B9", "extension_fees": "K16",
        "recap_opening": "K20", "recap_base_rate": "B10",
        "recap_margin": "B11", "recap_interest": "K21",
        "bridge_interest": "K13",
    }
    formulas = get_excel_formulas(refs)
    assert "bridge_interest" in formulas
    assert formulas["bridge_interest"].startswith("=")
