"""Tests for DSRA_001 — Debt Service Reserve Account / Facility."""

import pytest
from modules.debt.DSRA_001 import Inputs, Outputs, calculate, get_excel_formulas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    mode="cash",
    periods=60,
    target_months=6,
    debt_service=None,
    repayment_start_period=12,
    repayment_end_period=None,
    commitment_fee_rate=0.0075,
    arrangement_fee_rate=0.01,
    dsrf_tenor_years=10,
):
    if debt_service is None:
        # 12 months construction (0 DS), then constant DS
        ds = [0.0] * 12 + [1000.0] * (periods - 12)
        debt_service = ds[:periods]
    return Inputs(
        periods=periods,
        start_year=2026,
        start_month=1,
        mode=mode,
        target_months=target_months,
        debt_service=debt_service,
        repayment_start_period=repayment_start_period,
        repayment_end_period=repayment_end_period,
        commitment_fee_rate=commitment_fee_rate,
        arrangement_fee_rate=arrangement_fee_rate,
        dsrf_tenor_years=dsrf_tenor_years,
    )


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_validation_wrong_ds_length():
    with pytest.raises(ValueError, match="debt_service length"):
        Inputs(
            periods=10,
            start_year=2026,
            start_month=1,
            mode="cash",
            target_months=6,
            debt_service=[100.0] * 5,
            repayment_start_period=0,
        )


def test_validation_invalid_mode():
    with pytest.raises(ValueError, match="mode must be one of"):
        Inputs(
            periods=10,
            start_year=2026,
            start_month=1,
            mode="invalid",
            target_months=6,
            debt_service=[100.0] * 10,
            repayment_start_period=0,
        )


def test_validation_rep_end_before_start():
    with pytest.raises(ValueError, match="repayment_end_period must be"):
        Inputs(
            periods=10,
            start_year=2026,
            start_month=1,
            mode="cash",
            target_months=6,
            debt_service=[100.0] * 10,
            repayment_start_period=5,
            repayment_end_period=3,
        )


# ---------------------------------------------------------------------------
# Output structure tests
# ---------------------------------------------------------------------------

def test_output_is_outputs():
    out = calculate(_make_inputs())
    assert isinstance(out, Outputs)


def test_output_array_lengths():
    n = 60
    out = calculate(_make_inputs(periods=n))
    for field in ("target_balance", "actual_balance", "top_up", "release",
                   "commitment_fee", "arrangement_fee", "net_cash_impact"):
        assert len(getattr(out, field)) == n, f"{field} length mismatch"


# ---------------------------------------------------------------------------
# DSRA cash mode tests
# ---------------------------------------------------------------------------

def test_dsra_cash_target_during_construction_is_zero():
    out = calculate(_make_inputs(mode="cash"))
    for p in range(12):
        assert out.target_balance[p] == 0.0


def test_dsra_cash_target_at_repayment_start():
    """Target at period 12 = sum of DS for periods 12..17 = 6 x 1000."""
    out = calculate(_make_inputs(mode="cash", target_months=6))
    assert out.target_balance[12] == pytest.approx(6000.0)


def test_dsra_cash_top_up_at_start():
    """First period of repayment needs full target funded."""
    out = calculate(_make_inputs(mode="cash"))
    assert out.top_up[12] == pytest.approx(6000.0)


def test_dsra_cash_no_top_up_when_funded():
    """After initial funding, target stays flat — no further top-ups."""
    out = calculate(_make_inputs(mode="cash"))
    # Periods 13-47: DS=1000, 6-month target=6000, already have 6000
    for p in range(13, 48):
        assert out.top_up[p] == pytest.approx(0.0)


def test_dsra_cash_release_near_maturity():
    """As remaining DS shrinks below 6 months, target drops — release."""
    out = calculate(_make_inputs(mode="cash", periods=60, repayment_end_period=55))
    # At period 53: only 2 months of DS left — target = 2000, release = 4000
    assert out.target_balance[53] == pytest.approx(2000.0)
    assert out.release[53] > 0.0


def test_dsra_cash_balance_matches_target():
    """Actual balance should always match target in normal operation."""
    out = calculate(_make_inputs(mode="cash"))
    for p in range(60):
        assert out.actual_balance[p] == pytest.approx(out.target_balance[p], abs=0.01)


def test_dsra_cash_fully_funded():
    out = calculate(_make_inputs(mode="cash"))
    assert out.fully_funded is True


def test_dsra_cash_net_impact_sums_correctly():
    """net_cash_impact = -top_up + release for cash mode."""
    out = calculate(_make_inputs(mode="cash"))
    for p in range(60):
        expected = -out.top_up[p] + out.release[p]
        assert out.net_cash_impact[p] == pytest.approx(expected)


def test_dsra_cash_zero_fees():
    """Cash mode has no commitment or arrangement fees."""
    out = calculate(_make_inputs(mode="cash"))
    assert out.total_commitment_fees == 0.0
    assert out.total_arrangement_fees == 0.0
    assert all(f == 0.0 for f in out.commitment_fee)
    assert all(f == 0.0 for f in out.arrangement_fee)


def test_dsra_cash_max_target():
    out = calculate(_make_inputs(mode="cash"))
    assert out.max_target == pytest.approx(6000.0)


# ---------------------------------------------------------------------------
# DSRF facility mode tests
# ---------------------------------------------------------------------------

def test_dsrf_no_cash_balance():
    """Facility mode: no actual cash reserved."""
    out = calculate(_make_inputs(mode="facility"))
    assert all(b == 0.0 for b in out.actual_balance)
    assert all(t == 0.0 for t in out.top_up)
    assert all(r == 0.0 for r in out.release)


def test_dsrf_commitment_fee():
    """Commitment fee = target x rate / 12."""
    out = calculate(_make_inputs(mode="facility", commitment_fee_rate=0.0075))
    # Period 12: target=6000, fee = 6000 * 0.0075 / 12 = 3.75
    assert out.commitment_fee[12] == pytest.approx(3.75)


def test_dsrf_commitment_fee_zero_during_construction():
    out = calculate(_make_inputs(mode="facility"))
    for p in range(12):
        assert out.commitment_fee[p] == 0.0


def test_dsrf_arrangement_fee():
    """Arrangement fee at repayment start = target x rate."""
    out = calculate(_make_inputs(mode="facility", arrangement_fee_rate=0.01))
    assert out.arrangement_fee[12] == pytest.approx(60.0)  # 6000 * 0.01


def test_dsrf_arrangement_fee_only_once():
    out = calculate(_make_inputs(mode="facility", arrangement_fee_rate=0.01))
    assert sum(1 for f in out.arrangement_fee if f > 0) == 1


def test_dsrf_always_fully_funded():
    out = calculate(_make_inputs(mode="facility"))
    assert out.fully_funded is True


def test_dsrf_net_cash_impact():
    """Net cash = -(commitment + arrangement) in facility mode."""
    out = calculate(_make_inputs(mode="facility"))
    for p in range(60):
        expected = -(out.commitment_fee[p] + out.arrangement_fee[p])
        assert out.net_cash_impact[p] == pytest.approx(expected)


def test_dsrf_tenor_limits_fees():
    """Commitment fees stop after dsrf_tenor_years."""
    out = calculate(_make_inputs(
        mode="facility",
        periods=240,  # 20 years
        debt_service=[0.0] * 12 + [1000.0] * 228,
        repayment_start_period=12,
        dsrf_tenor_years=5,
    ))
    # Fees should stop at period 12 + 5*12 = 72
    assert out.commitment_fee[71] > 0
    assert out.commitment_fee[72] == 0.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_all_zero_debt_service():
    out = calculate(_make_inputs(debt_service=[0.0] * 60))
    assert all(t == 0.0 for t in out.target_balance)
    assert out.max_target == 0.0
    assert out.fully_funded is True


def test_target_months_equals_one():
    out = calculate(_make_inputs(target_months=1))
    assert out.target_balance[12] == pytest.approx(1000.0)


def test_short_model_fewer_than_target_months():
    """Model shorter than target_months of remaining DS."""
    out = calculate(_make_inputs(
        periods=15,
        target_months=6,
        debt_service=[0.0] * 12 + [1000.0] * 3,
        repayment_start_period=12,
    ))
    # Period 12: only 3 months of DS left — target = 3000
    assert out.target_balance[12] == pytest.approx(3000.0)


def test_varying_debt_service():
    """Target correctly sums varying forward DS."""
    ds = [0.0] * 2 + [100, 200, 300, 400, 500, 600, 700, 800]
    out = calculate(Inputs(
        periods=10,
        start_year=2026,
        start_month=1,
        mode="cash",
        target_months=3,
        debt_service=ds,
        repayment_start_period=2,
    ))
    # Period 2: sum of DS[2:5] = 100+200+300 = 600
    assert out.target_balance[2] == pytest.approx(600.0)
    # Period 7: sum of DS[7:10] = 600+700+800 = 2100
    assert out.target_balance[7] == pytest.approx(2100.0)


# ---------------------------------------------------------------------------
# Excel formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_dict():
    refs = {
        "ds_range": "Debt!$C$11:$FZ$11",
        "period_range": "Debt!$C$3:$FZ$3",
        "period_ref": "C3",
        "target_months": "6",
        "target": "C45",
        "prev_actual": "B46",
        "top_up": "C47",
        "release": "C48",
        "commitment_rate": "$B$50",
    }
    formulas = get_excel_formulas(refs)
    assert isinstance(formulas, dict)
    assert "target_balance" in formulas
    assert "top_up" in formulas
    assert "commitment_fee" in formulas
