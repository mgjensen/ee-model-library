"""Tests for DECOM_PROVISION_001 — decommissioning provision + sinking fund."""

import pytest
from modules.costs.DECOM_PROVISION_001 import (
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
        periods=60,
        start_year=2025,
        start_month=1,
        active=True,
        provision_amount=5_000.0,
        provision_start_period=0,
        provision_end_period=59,
        disposal_cost=5_000.0,
        disposal_period=59,
        sinking_fund_active=False,
        sinking_fund_target=0.0,
        sinking_fund_start_period=0,
        sinking_fund_end_period=None,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_provision_end_exceeds_periods():
    with pytest.raises(ValueError, match="provision_end_period"):
        Inputs(periods=12, start_year=2025, start_month=1,
               provision_end_period=12)


def test_disposal_period_exceeds_periods():
    with pytest.raises(ValueError, match="disposal_period"):
        Inputs(periods=12, start_year=2025, start_month=1,
               disposal_period=12)


def test_sinking_fund_end_exceeds_periods():
    with pytest.raises(ValueError, match="sinking_fund_end_period"):
        Inputs(periods=12, start_year=2025, start_month=1,
               sinking_fund_end_period=12)


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 60
    out = calculate(_make_inputs(periods=n))
    for field in ("provision_balance", "unwinding", "disposal_cost_monthly",
                  "fund_balance", "fund_contribution", "fund_release",
                  "net_cash_impact"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------

def test_provision_balance_during_active():
    out = calculate(_make_inputs(provision_start_period=6, provision_end_period=50))
    for p in range(6):
        assert out.provision_balance[p] == pytest.approx(0.0)
    for p in range(6, 51):
        assert out.provision_balance[p] == pytest.approx(5_000.0)


def test_provision_unwinds_at_end():
    out = calculate(_make_inputs(provision_end_period=59))
    assert out.unwinding[59] == pytest.approx(-5_000.0)


def test_provision_zero_after_end():
    out = calculate(_make_inputs(provision_end_period=30, periods=60))
    for p in range(31, 60):
        assert out.provision_balance[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Disposal cost
# ---------------------------------------------------------------------------

def test_disposal_cost_spike():
    out = calculate(_make_inputs(disposal_cost=8_000.0, disposal_period=59))
    assert out.disposal_cost_monthly[59] == pytest.approx(8_000.0)
    assert out.disposal_cost_monthly[0] == pytest.approx(0.0)


def test_total_disposal():
    out = calculate(_make_inputs(disposal_cost=8_000.0))
    assert out.total_disposal == pytest.approx(8_000.0)


# ---------------------------------------------------------------------------
# Sinking fund
# ---------------------------------------------------------------------------

def test_sinking_fund_even_contributions():
    out = calculate(_make_inputs(
        sinking_fund_active=True,
        sinking_fund_target=6_000.0,
        sinking_fund_start_period=0,
        sinking_fund_end_period=58,
        disposal_period=59,
    ))
    expected_monthly = 6_000.0 / 59  # 59 contribution periods (0..58)
    assert out.fund_contribution[0] == pytest.approx(expected_monthly)
    assert out.fund_contribution[30] == pytest.approx(expected_monthly)
    assert out.fund_contribution[59] == pytest.approx(0.0)  # disposal period


def test_sinking_fund_release_at_disposal():
    out = calculate(_make_inputs(
        sinking_fund_active=True,
        sinking_fund_target=6_000.0,
        sinking_fund_start_period=0,
        sinking_fund_end_period=58,
        disposal_period=59,
    ))
    assert out.fund_release[59] == pytest.approx(6_000.0)
    assert out.fund_balance[59] == pytest.approx(0.0)


def test_total_contributions():
    out = calculate(_make_inputs(
        sinking_fund_active=True,
        sinking_fund_target=3_000.0,
        sinking_fund_start_period=0,
        sinking_fund_end_period=58,
    ))
    assert out.total_contributions == pytest.approx(3_000.0)


# ---------------------------------------------------------------------------
# Net cash impact
# ---------------------------------------------------------------------------

def test_net_cash_impact_formula():
    out = calculate(_make_inputs(
        sinking_fund_active=True,
        sinking_fund_target=5_000.0,
        sinking_fund_start_period=0,
        sinking_fund_end_period=58,
        disposal_cost=5_000.0,
        disposal_period=59,
    ))
    for p in range(60):
        expected = -out.fund_contribution[p] + out.fund_release[p] - out.disposal_cost_monthly[p]
        assert out.net_cash_impact[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Inactive
# ---------------------------------------------------------------------------

def test_inactive_all_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(v == 0.0 for v in out.provision_balance)
    assert all(v == 0.0 for v in out.fund_contribution)
    assert out.total_contributions == pytest.approx(0.0)
    assert out.total_disposal == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "period": "K1", "prov_start": "B5", "prov_end": "B6",
        "provision_amount": "B7", "disposal_period": "B8",
        "disposal_cost": "B9", "sf_start": "B10", "sf_end": "B11",
        "sf_target": "B12", "num_contrib": "B13",
        "fund_balance": "K20", "fund_contribution": "K21",
        "fund_release": "K22",
    }
    formulas = get_excel_formulas(refs)
    assert "provision_balance" in formulas
    assert "net_cash_impact" in formulas
    assert formulas["provision_balance"].startswith("=")
