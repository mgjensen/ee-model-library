"""Tests for VALUATION_001 — EV bridge / purchase price calculation."""

import pytest
from modules.statements.VALUATION_001 import Inputs, Outputs, calculate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inputs(
    periods=120,
    closing_period=0,
    discount_rate=0.085,
    capacity_mw=50.0,
    capacity_mwh=0.0,
    fcf=None,
    debt=20_000.0,
    cash=1_000.0,
    nwc=500.0,
    other=0.0,
    remaining_capex=0.0,
    investor_pct=0.50,
) -> Inputs:
    if fcf is None:
        fcf = [500.0] * periods
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        closing_period=closing_period,
        discount_rate=discount_rate,
        capacity_mw=capacity_mw,
        capacity_mwh=capacity_mwh,
        fcf_monthly=fcf,
        debt_outstanding_at_closing=debt,
        cash_at_closing=cash,
        nwc_at_closing=nwc,
        other_adjustments=other,
        remaining_capex_after_closing=remaining_capex,
        investor_pct=investor_pct,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ev_npv_calculation():
    """EV should equal NPV of FCF from closing onward."""
    inp = _base_inputs(periods=12, fcf=[1000.0] * 12, closing_period=0, discount_rate=0.10)
    out = calculate(inp)
    # Manual NPV check
    mr = (1.10) ** (1.0 / 12.0) - 1.0
    expected_ev = sum(1000.0 / (1.0 + mr) ** t for t in range(12))
    assert abs(out.ev - expected_ev) < 0.01


def test_purchase_price_bridge():
    """Purchase price = EV - debt + cash - NWC - other."""
    inp = _base_inputs(
        periods=12, fcf=[1000.0] * 12, closing_period=0,
        debt=5000.0, cash=800.0, nwc=200.0, other=100.0,
    )
    out = calculate(inp)
    expected_pp = out.ev - 5000.0 + 800.0 - 200.0 - 100.0
    assert abs(out.purchase_price_100pct - expected_pp) < 0.01


def test_ev_per_mw():
    """EV per MW = EV / capacity_mw."""
    inp = _base_inputs(periods=12, fcf=[1000.0] * 12, capacity_mw=25.0)
    out = calculate(inp)
    assert abs(out.ev_per_mw - out.ev / 25.0) < 0.01


def test_ev_per_mwh_zero_bess():
    """EV per MWh should be 0 when capacity_mwh is 0 (no BESS)."""
    inp = _base_inputs(periods=12, fcf=[1000.0] * 12, capacity_mwh=0.0)
    out = calculate(inp)
    assert out.ev_per_mwh == 0.0


def test_gearing_calculation():
    """Gearing = debt / (debt + purchase_price)."""
    inp = _base_inputs(
        periods=12, fcf=[1000.0] * 12, closing_period=0,
        debt=10_000.0, cash=0.0, nwc=0.0, other=0.0,
    )
    out = calculate(inp)
    expected_gearing = 10_000.0 / (10_000.0 + out.purchase_price_100pct)
    assert abs(out.gearing_at_closing - expected_gearing) < 1e-6


def test_equity_ticket():
    """Total equity ticket = purchase price + remaining capex."""
    inp = _base_inputs(
        periods=12, fcf=[1000.0] * 12,
        remaining_capex=3000.0,
    )
    out = calculate(inp)
    assert abs(out.total_equity_ticket_100pct - (out.purchase_price_100pct + 3000.0)) < 0.01
    assert abs(out.total_equity_ticket_investor - (out.purchase_price_investor + 3000.0 * 0.5)) < 0.01


def test_investor_pct_50():
    """Investor purchase price and ticket should be 50% of 100% values."""
    inp = _base_inputs(periods=12, fcf=[1000.0] * 12, investor_pct=0.50, remaining_capex=2000.0)
    out = calculate(inp)
    assert abs(out.purchase_price_investor - out.purchase_price_100pct * 0.50) < 0.01
    assert abs(out.total_equity_ticket_investor - (out.purchase_price_100pct * 0.50 + 2000.0 * 0.50)) < 0.01


def test_zero_debt_all_equity():
    """With zero debt, gearing is 0 and purchase price equals EV + cash - NWC."""
    inp = _base_inputs(
        periods=12, fcf=[1000.0] * 12,
        debt=0.0, cash=500.0, nwc=100.0, other=0.0,
    )
    out = calculate(inp)
    assert out.gearing_at_closing == 0.0
    expected_pp = out.ev + 500.0 - 100.0
    assert abs(out.purchase_price_100pct - expected_pp) < 0.01


def test_output_structure():
    """calculate() returns an Outputs instance with all expected fields."""
    inp = _base_inputs(periods=12, fcf=[500.0] * 12)
    out = calculate(inp)
    assert isinstance(out, Outputs)
    for field in [
        "ev", "ev_per_mw", "ev_per_mwh",
        "purchase_price_100pct", "purchase_price_investor",
        "total_equity_ticket_100pct", "total_equity_ticket_investor",
        "gearing_at_closing", "remaining_capex_after_closing",
    ]:
        assert hasattr(out, field)


def test_high_discount_rate_lowers_ev():
    """A higher discount rate should produce a lower EV."""
    fcf = [500.0] * 120
    out_low = calculate(_base_inputs(periods=120, fcf=fcf, discount_rate=0.05))
    out_high = calculate(_base_inputs(periods=120, fcf=fcf, discount_rate=0.15))
    assert out_high.ev < out_low.ev
