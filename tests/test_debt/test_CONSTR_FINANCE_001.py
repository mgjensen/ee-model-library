"""Tests for CONSTR_FINANCE_001 — construction finance bullet facility."""

import pytest
from modules.debt.CONSTR_FINANCE_001 import Inputs, Outputs, calculate, get_excel_formulas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=60,
    facility=100_000.0,
    construction_start=0,
    cod_period=12,
    equity_first=True,
    total_equity=40_000.0,
    base_rate=0.03,
    margin=0.016,
    commitment_fee_rate=0.0056,
    arrangement_fee_pct=0.008,
    capex_monthly=None,
    active=True,
):
    if capex_monthly is None:
        # 12 months construction, 10k/month = 120k total capex
        capex_monthly = [10_000.0] * cod_period + [0.0] * (periods - cod_period)
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        active=active,
        facility_DKKk=facility,
        construction_start_period=construction_start,
        cod_period=cod_period,
        capex_monthly=capex_monthly,
        equity_first=equity_first,
        total_equity_DKKk=total_equity,
        base_rate=base_rate,
        margin=margin,
        commitment_fee_rate=commitment_fee_rate,
        arrangement_fee_pct=arrangement_fee_pct,
    )


# ---------------------------------------------------------------------------
# Inactive
# ---------------------------------------------------------------------------

def test_inactive_returns_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(v == 0.0 for v in out.opening_balance)
    assert out.total_interest == 0.0
    assert out.peak_balance == 0.0


# ---------------------------------------------------------------------------
# Bullet repayment
# ---------------------------------------------------------------------------

def test_bullet_repayment_at_cod():
    """Entire balance repaid at COD."""
    out = calculate(_make_inputs())
    assert out.repayment[12] > 0
    # Only one repayment period
    assert sum(1 for r in out.repayment if r > 0) == 1


def test_balance_zero_after_cod():
    out = calculate(_make_inputs())
    for p in range(13, 60):
        assert out.closing_balance[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Equity sequencing
# ---------------------------------------------------------------------------

def test_equity_first_draws_equity_before_debt():
    """With 40k equity and 10k/month capex, first 4 months = equity only."""
    out = calculate(_make_inputs(total_equity=40_000.0))
    # Periods 0-3: 10k capex each, equity covers all
    for p in range(4):
        assert out.equity_drawn[p] == pytest.approx(10_000.0)
        assert out.drawdown[p] == pytest.approx(0.0)
    # Period 4+: equity exhausted, debt kicks in
    assert out.drawdown[4] > 0


def test_equity_last_draws_debt_before_equity():
    """Equity last: debt drawn first."""
    out = calculate(_make_inputs(equity_first=False))
    # First period: 10k capex, debt drawn first
    assert out.drawdown[0] > 0
    # Equity drawn only when facility exhausted
    total_debt = sum(out.drawdown)
    assert total_debt == pytest.approx(min(100_000.0, 120_000.0 - 0), rel=0.1)


# ---------------------------------------------------------------------------
# Drawdown cap
# ---------------------------------------------------------------------------

def test_drawdown_capped_at_facility():
    """Cumulative drawdown never exceeds facility."""
    out = calculate(_make_inputs(facility=50_000.0, total_equity=70_000.0))
    cum_draw = 0.0
    for p in range(60):
        cum_draw += out.drawdown[p]
        assert cum_draw <= 50_000.0 + 0.01


# ---------------------------------------------------------------------------
# Interest
# ---------------------------------------------------------------------------

def test_interest_on_opening_balance():
    out = calculate(_make_inputs())
    for p in range(60):
        if out.opening_balance[p] > 1e-9:
            expected = out.opening_balance[p] * (0.03 + 0.016) / 12.0
            assert out.interest[p] == pytest.approx(expected, rel=1e-6)
        else:
            assert out.interest[p] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Commitment fee
# ---------------------------------------------------------------------------

def test_commitment_fee_on_undrawn():
    out = calculate(_make_inputs())
    # Period 0: balance=0, undrawn=100k, fee = 100k * 0.0056 / 12
    assert out.commitment_fee[0] == pytest.approx(100_000.0 * 0.0056 / 12.0, rel=1e-4)


def test_commitment_fee_zero_when_fully_drawn():
    """If facility fully drawn, commitment fee = 0."""
    # 100k facility, 100k capex in 10 months = facility fully drawn
    out = calculate(_make_inputs(
        facility=100_000.0, total_equity=20_000.0,
    ))
    # Once balance == facility, undrawn = 0
    for p in range(60):
        if out.opening_balance[p] >= 100_000.0 - 0.01:
            assert out.commitment_fee[p] == pytest.approx(0.0, abs=0.01)


def test_commitment_fee_zero_after_cod():
    out = calculate(_make_inputs())
    for p in range(12, 60):
        assert out.commitment_fee[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Arrangement fee
# ---------------------------------------------------------------------------

def test_arrangement_fee_at_close():
    out = calculate(_make_inputs(arrangement_fee_pct=0.008))
    assert out.arrangement_fee[0] == pytest.approx(100_000.0 * 0.008)
    assert sum(out.arrangement_fee) == pytest.approx(100_000.0 * 0.008)


# ---------------------------------------------------------------------------
# IDC
# ---------------------------------------------------------------------------

def test_idc_cumulative_equals_sum_interest():
    out = calculate(_make_inputs())
    # IDC at COD should equal sum of interest during construction
    construction_interest = sum(out.interest[p] for p in range(12))
    assert out.idc_cumulative[12] == pytest.approx(construction_interest)
    assert out.total_idc == pytest.approx(construction_interest)


# ---------------------------------------------------------------------------
# Peak balance
# ---------------------------------------------------------------------------

def test_peak_balance_matches_max():
    out = calculate(_make_inputs())
    actual_peak = max(out.opening_balance[p] + out.drawdown[p] for p in range(60))
    assert out.peak_balance == pytest.approx(actual_peak)


# ---------------------------------------------------------------------------
# No activity after COD
# ---------------------------------------------------------------------------

def test_no_drawdown_after_cod():
    out = calculate(_make_inputs())
    for p in range(13, 60):
        assert out.drawdown[p] == 0.0


# ---------------------------------------------------------------------------
# Balance mechanics
# ---------------------------------------------------------------------------

def test_total_drawdown_equals_capex_minus_equity():
    """Equity first: total debt drawdown = total capex - equity drawn."""
    out = calculate(_make_inputs())
    total_capex = sum(out.drawdown[p] + out.equity_drawn[p] for p in range(12))
    assert total_capex == pytest.approx(120_000.0)


def test_balance_mechanics():
    """closing = opening + draw - repay."""
    out = calculate(_make_inputs())
    for p in range(60):
        expected = out.opening_balance[p] + out.drawdown[p] - out.repayment[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=0.01)


def test_opening_equals_prev_closing():
    out = calculate(_make_inputs())
    for p in range(1, 60):
        assert out.opening_balance[p] == pytest.approx(out.closing_balance[p - 1], abs=0.01)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validation_capex_length():
    with pytest.raises(ValueError, match="capex_monthly length"):
        Inputs(
            periods=60, start_year=2026, start_month=1,
            facility_DKKk=100_000.0,
            construction_start_period=0, cod_period=12,
            capex_monthly=[10_000.0] * 10,  # wrong length
            total_equity_DKKk=40_000.0, margin=0.016,
        )


def test_validation_cod_before_start():
    with pytest.raises(ValueError, match="cod_period must be"):
        Inputs(
            periods=60, start_year=2026, start_month=1,
            facility_DKKk=100_000.0,
            construction_start_period=12, cod_period=6,
            capex_monthly=[0.0] * 60,
            total_equity_DKKk=40_000.0, margin=0.016,
        )


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_dict():
    refs = {
        "capex": "C30", "equity_draw": "C88", "facility": "$B$5",
        "balance": "C82", "opening": "C82", "base_rate": "C20",
        "margin": "$B$6", "commit_rate": "$B$7", "period": "C3",
        "cod": "12", "drawdown": "C83", "repayment": "C84",
        "construction_start": "0", "fee_pct": "$B$8",
    }
    formulas = get_excel_formulas(refs)
    assert isinstance(formulas, dict)
    for key in ("drawdown_equity_first", "interest", "commitment_fee",
                "repayment_bullet", "closing_balance", "arrangement_fee"):
        assert key in formulas
        assert formulas[key].startswith("=")
