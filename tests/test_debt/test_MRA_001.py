"""Tests for MRA_001 — Maintenance Reserve Account."""

import pytest
from modules.debt.MRA_001 import (
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
        periods=36,
        start_year=2025,
        start_month=1,
        active=True,
        target_balance=1_000.0,
        funding_start_period=6,
        maintenance_capex=[0.0] * 36,
    )
    defaults.update(overrides)
    return Inputs(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_maintenance_capex_length_mismatch():
    with pytest.raises(ValueError, match="maintenance_capex"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            maintenance_capex=[100.0] * 6,
        )


def test_empty_maintenance_capex_accepted():
    inp = Inputs(periods=12, start_year=2025, start_month=1, maintenance_capex=[])
    assert inp.periods == 12


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 36
    out = calculate(_make_inputs(periods=n))
    for field in ("balance", "funding", "release", "net_cash_impact"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Funding mechanics
# ---------------------------------------------------------------------------

def test_no_funding_before_start():
    out = calculate(_make_inputs(funding_start_period=12))
    for p in range(12):
        assert out.funding[p] == pytest.approx(0.0)
        assert out.balance[p] == pytest.approx(0.0)


def test_initial_funding_to_target():
    out = calculate(_make_inputs(target_balance=500.0, funding_start_period=3))
    assert out.funding[3] == pytest.approx(500.0)
    assert out.balance[3] == pytest.approx(500.0)


def test_no_additional_funding_when_at_target():
    out = calculate(_make_inputs(target_balance=500.0, funding_start_period=0))
    # Period 0: funded to target. Period 1: no capex, no additional funding needed
    assert out.funding[0] == pytest.approx(500.0)
    assert out.funding[1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Release mechanics
# ---------------------------------------------------------------------------

def test_release_for_maintenance_capex():
    capex = [0.0] * 36
    capex[12] = 300.0
    out = calculate(_make_inputs(target_balance=1_000.0, funding_start_period=0,
                                  maintenance_capex=capex))
    assert out.release[12] == pytest.approx(300.0)


def test_release_capped_at_balance():
    capex = [0.0] * 36
    capex[12] = 5_000.0  # More than target balance
    out = calculate(_make_inputs(target_balance=1_000.0, funding_start_period=0,
                                  maintenance_capex=capex))
    assert out.release[12] == pytest.approx(1_000.0)


def test_top_up_after_release():
    capex = [0.0] * 36
    capex[12] = 400.0
    out = calculate(_make_inputs(target_balance=1_000.0, funding_start_period=0,
                                  maintenance_capex=capex))
    # Release 400, then top up 400 to get back to 1000
    assert out.release[12] == pytest.approx(400.0)
    assert out.funding[12] == pytest.approx(400.0)
    assert out.balance[12] == pytest.approx(1_000.0)


# ---------------------------------------------------------------------------
# Net cash impact
# ---------------------------------------------------------------------------

def test_net_cash_impact():
    capex = [0.0] * 36
    capex[12] = 200.0
    out = calculate(_make_inputs(target_balance=1_000.0, funding_start_period=0,
                                  maintenance_capex=capex))
    for p in range(36):
        expected = -out.funding[p] + out.release[p]
        assert out.net_cash_impact[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Inactive / zero target
# ---------------------------------------------------------------------------

def test_inactive_all_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(v == 0.0 for v in out.balance)
    assert out.total_funding == pytest.approx(0.0)


def test_zero_target_all_zeros():
    out = calculate(_make_inputs(target_balance=0.0))
    assert all(v == 0.0 for v in out.balance)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def test_total_funding_equals_sum():
    out = calculate(_make_inputs())
    assert out.total_funding == pytest.approx(sum(out.funding))


def test_total_release_equals_sum():
    capex = [0.0] * 36
    capex[20] = 500.0
    out = calculate(_make_inputs(maintenance_capex=capex))
    assert out.total_release == pytest.approx(sum(out.release))


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "maintenance_capex": "K30", "prev_balance": "K10",
        "target": "B5", "release": "K11", "funding": "K12",
    }
    formulas = get_excel_formulas(refs)
    assert "balance" in formulas
    assert formulas["balance"].startswith("=")
