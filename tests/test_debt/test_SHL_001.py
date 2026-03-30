"""Tests for SHL_001 — shareholder loan module."""

import pytest
from modules.debt.SHL_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=120,
    start_year=2025,
    start_month=1,
    shl_pct=0.80,
    margin=0.0795,
    accrued=True,
    equity_total=100_000.0,
    n_draw=12,
    repayment_schedule=None,
):
    """Standard SHL inputs with equity drawn evenly over n_draw months."""
    eq = [equity_total / n_draw] * n_draw + [0.0] * (periods - n_draw)
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        shl_pct_of_equity=shl_pct,
        margin=margin,
        accrued=accrued,
        equity_contributed=eq,
        repayment_schedule=repayment_schedule,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_equity_contributed_length_mismatch():
    with pytest.raises(ValueError, match="equity_contributed"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            equity_contributed=[100.0] * 6,  # wrong length
        )


def test_repayment_schedule_length_mismatch():
    with pytest.raises(ValueError, match="repayment_schedule"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            equity_contributed=[100.0] * 12,
            repayment_schedule=[0.0] * 6,  # wrong length
        )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 120
    out = calculate(_make_inputs(periods=n))
    for field in ("opening_balance", "interest", "repayment",
                  "closing_balance", "interest_cash", "total_cash_flow"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# SHL sizing
# ---------------------------------------------------------------------------

def test_initial_shl_is_pct_of_equity():
    out = calculate(_make_inputs(equity_total=200_000.0, shl_pct=0.80))
    assert out.initial_shl == pytest.approx(160_000.0)


def test_initial_shl_zero_equity():
    out = calculate(_make_inputs(equity_total=0.0, shl_pct=0.80))
    assert out.initial_shl == pytest.approx(0.0)
    assert all(v == pytest.approx(0.0) for v in out.opening_balance)


def test_initial_shl_full_equity():
    out = calculate(_make_inputs(equity_total=100_000.0, shl_pct=1.0))
    assert out.initial_shl == pytest.approx(100_000.0)


# ---------------------------------------------------------------------------
# Balance mechanics — accrued (PIK)
# ---------------------------------------------------------------------------

def test_accrued_interest_increases_balance():
    """With PIK, closing balance should exceed opening after interest."""
    out = calculate(_make_inputs(
        periods=24, n_draw=6, accrued=True, margin=0.10,
    ))
    # After drawdowns complete (period 6), balance should grow due to interest
    assert out.closing_balance[12] > out.opening_balance[12]


def test_accrued_interest_cash_is_zero():
    """PIK mode: no cash interest paid."""
    out = calculate(_make_inputs(accrued=True))
    assert all(v == pytest.approx(0.0) for v in out.interest_cash)


def test_accrued_closing_equals_opening_plus_interest_minus_repayment():
    out = calculate(_make_inputs(periods=24, n_draw=6, accrued=True))
    for p in range(24):
        expected = out.opening_balance[p] + out.interest[p] - out.repayment[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


def test_accrued_balance_compounds():
    """PIK balance should compound: closing > initial_shl after some periods."""
    out = calculate(_make_inputs(
        periods=60, n_draw=6, accrued=True, margin=0.10,
        equity_total=100_000.0, shl_pct=1.0,
    ))
    # After 60 months of compounding at 10%, balance should exceed initial
    # (but final period repays everything, so check period 58)
    assert out.opening_balance[58] > 100_000.0


# ---------------------------------------------------------------------------
# Balance mechanics — cash-pay
# ---------------------------------------------------------------------------

def test_cash_pay_interest_paid_each_period():
    """Cash-pay mode: interest_cash = interest."""
    out = calculate(_make_inputs(accrued=False))
    for p in range(len(out.interest)):
        assert out.interest_cash[p] == pytest.approx(out.interest[p])


def test_cash_pay_balance_flat_after_drawdown():
    """Cash-pay: balance stays constant after last drawdown (no compounding)."""
    out = calculate(_make_inputs(
        periods=60, n_draw=6, accrued=False,
        equity_total=100_000.0, shl_pct=1.0,
    ))
    # Between last drawdown and final repayment, balance = initial_shl
    for p in range(6, 59):
        assert out.closing_balance[p] == pytest.approx(100_000.0, abs=0.01)


def test_cash_pay_closing_equals_opening_minus_repayment():
    out = calculate(_make_inputs(periods=24, n_draw=6, accrued=False))
    for p in range(24):
        expected = out.opening_balance[p] - out.repayment[p]
        assert out.closing_balance[p] == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# Default repayment (final period bullet)
# ---------------------------------------------------------------------------

def test_default_repayment_in_final_period():
    """No repayment_schedule → full balance repaid in last period."""
    out = calculate(_make_inputs(periods=60, n_draw=6))
    # All repayment in period 59
    assert out.repayment[59] > 0
    assert all(out.repayment[p] == pytest.approx(0.0) for p in range(59))


def test_default_repayment_fully_repaid():
    out = calculate(_make_inputs(periods=60, n_draw=6))
    assert out.fully_repaid
    assert out.final_balance == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Custom repayment schedule
# ---------------------------------------------------------------------------

def test_custom_repayment_schedule():
    n = 24
    n_draw = 6
    equity_total = 100_000.0
    shl_pct = 1.0
    # Repay 10,000 per month from period 12 to 21 (10 months)
    sched = [0.0] * 12 + [10_000.0] * 10 + [0.0] * 2
    out = calculate(_make_inputs(
        periods=n, n_draw=n_draw, accrued=False, margin=0.0,
        equity_total=equity_total, shl_pct=shl_pct,
        repayment_schedule=sched,
    ))
    assert out.total_repayment == pytest.approx(100_000.0)
    assert out.final_balance == pytest.approx(0.0, abs=0.01)


def test_repayment_capped_at_balance():
    """Repayment can't exceed outstanding balance."""
    n = 12
    sched = [0.0] * 6 + [999_999.0] * 6  # way more than balance
    out = calculate(_make_inputs(
        periods=n, n_draw=6, accrued=False, margin=0.0,
        equity_total=10_000.0, shl_pct=1.0,
        repayment_schedule=sched,
    ))
    assert out.total_repayment == pytest.approx(10_000.0, abs=0.01)
    assert all(b >= -1e-6 for b in out.closing_balance)


# ---------------------------------------------------------------------------
# Total cash flow
# ---------------------------------------------------------------------------

def test_total_cash_flow_accrued():
    """Accrued: total_cash = repayment only (no interest cash)."""
    out = calculate(_make_inputs(accrued=True))
    for p in range(len(out.total_cash_flow)):
        assert out.total_cash_flow[p] == pytest.approx(out.repayment[p])


def test_total_cash_flow_cash_pay():
    """Cash-pay: total_cash = repayment + interest."""
    out = calculate(_make_inputs(accrued=False))
    for p in range(len(out.total_cash_flow)):
        expected = out.repayment[p] + out.interest_cash[p]
        assert out.total_cash_flow[p] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Interest calculation
# ---------------------------------------------------------------------------

def test_interest_on_opening_balance():
    out = calculate(_make_inputs(margin=0.12, periods=24, n_draw=6))
    r = 0.12 / 12.0
    for p in range(24):
        assert out.interest[p] == pytest.approx(out.opening_balance[p] * r, abs=1e-6)


def test_zero_margin_no_interest():
    out = calculate(_make_inputs(margin=0.0))
    assert all(v == pytest.approx(0.0) for v in out.interest)
    assert out.total_interest == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Balance never negative
# ---------------------------------------------------------------------------

def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)


def test_repayment_never_negative():
    out = calculate(_make_inputs())
    assert all(r >= -1e-9 for r in out.repayment)


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "closing_prev": "K9", "shl_drawdown": "K10",
        "opening_bal": "K11", "monthly_margin": "B5",
        "interest": "K12", "repayment": "K13",
    }
    formulas = get_excel_formulas(refs)
    for key in ("opening_balance", "interest",
                "closing_balance_accrued", "closing_balance_cash"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# Multi-tranche tests
# ---------------------------------------------------------------------------

from modules.debt.SHL_001 import SHLTranche


class TestMultiTranche:

    def test_tranches_none_uses_single_tranche_logic(self):
        """Backward compat: tranches=None runs old path."""
        inp = Inputs(
            periods=60, start_year=2026, start_month=1,
            shl_pct_of_equity=0.80, margin=0.07, accrued=True,
            equity_contributed=[1000.0] * 12 + [0.0] * 48,
        )
        out = calculate(inp)
        assert out.tranche_outputs is None
        assert out.initial_shl == pytest.approx(0.80 * 12000.0)

    def test_single_tranche_via_tranches_matches_legacy(self):
        """One tranche in list should produce same aggregate as legacy."""
        eq = [1000.0] * 12 + [0.0] * 48
        legacy = calculate(Inputs(
            periods=60, start_year=2026, start_month=1,
            shl_pct_of_equity=0.80, margin=0.07, accrued=True,
            equity_contributed=eq,
        ))
        multi = calculate(Inputs(
            periods=60, start_year=2026, start_month=1,
            equity_contributed=eq,  # ignored when tranches set
            tranches=[SHLTranche(
                name="EV", shl_pct_of_equity=0.80,
                equity_contributed=eq, margin=0.07, accrued=True,
            )],
        ))
        assert multi.initial_shl == pytest.approx(legacy.initial_shl, rel=1e-6)
        assert multi.total_interest == pytest.approx(legacy.total_interest, rel=1e-4)

    def test_dual_tranche_ev_plus_bess(self):
        n = 120
        eq_ev = [5000.0] + [0.0] * (n - 1)  # lump sum
        eq_bess = [0.0] * 60 + [2000.0] * 6 + [0.0] * 54
        out = calculate(Inputs(
            periods=n, start_year=2026, start_month=1,
            equity_contributed=[0.0] * n,
            tranches=[
                SHLTranche(name="EV", fixed_amount_DKKk=5000.0,
                           drawdown_schedule=eq_ev, margin=0.08, accrued=True),
                SHLTranche(name="BESS", shl_pct_of_equity=1.0,
                           equity_contributed=eq_bess, margin=0.06, accrued=False),
            ],
        ))
        assert out.tranche_outputs is not None
        assert len(out.tranche_outputs) == 2
        assert out.tranche_outputs[0]["name"] == "EV"
        assert out.tranche_outputs[1]["name"] == "BESS"
        assert out.initial_shl == pytest.approx(5000.0 + 12000.0)

    def test_fixed_amount_tranche_drawdown(self):
        n = 60
        dd = [10_000.0] + [0.0] * 59
        out = calculate(Inputs(
            periods=n, start_year=2026, start_month=1,
            equity_contributed=[0.0] * n,
            tranches=[SHLTranche(
                name="EV", fixed_amount_DKKk=10_000.0,
                drawdown_schedule=dd, margin=0.07, accrued=True,
            )],
        ))
        assert out.initial_shl == pytest.approx(10_000.0)
        assert out.opening_balance[0] == pytest.approx(10_000.0)

    def test_aggregated_outputs_sum_of_tranches(self):
        n = 60
        eq = [500.0] * 12 + [0.0] * 48
        out = calculate(Inputs(
            periods=n, start_year=2026, start_month=1,
            equity_contributed=eq,
            tranches=[
                SHLTranche(name="A", shl_pct_of_equity=0.50,
                           equity_contributed=eq, margin=0.06, accrued=True),
                SHLTranche(name="B", shl_pct_of_equity=0.30,
                           equity_contributed=eq, margin=0.08, accrued=False),
            ],
        ))
        for p in range(n):
            a = out.tranche_outputs[0]["opening_balance"][p]
            b = out.tranche_outputs[1]["opening_balance"][p]
            assert out.opening_balance[p] == pytest.approx(a + b, abs=0.01)

    def test_tranche_outputs_populated(self):
        n = 60
        eq = [1000.0] * 12 + [0.0] * 48
        out = calculate(Inputs(
            periods=n, start_year=2026, start_month=1,
            equity_contributed=eq,
            tranches=[SHLTranche(name="T1", shl_pct_of_equity=0.80,
                                 equity_contributed=eq)],
        ))
        assert out.tranche_outputs is not None
        t = out.tranche_outputs[0]
        assert "name" in t
        assert len(t["opening_balance"]) == n

    def test_validation_rejects_both_sizing(self):
        with pytest.raises(ValueError, match="exactly one"):
            Inputs(
                periods=60, start_year=2026, start_month=1,
                equity_contributed=[0.0] * 60,
                tranches=[SHLTranche(
                    name="bad", fixed_amount_DKKk=1000.0,
                    drawdown_schedule=[1000.0] + [0.0] * 59,
                    shl_pct_of_equity=0.5,
                    equity_contributed=[100.0] * 60,
                )],
            )

    def test_validation_rejects_neither_sizing(self):
        with pytest.raises(ValueError, match="exactly one"):
            Inputs(
                periods=60, start_year=2026, start_month=1,
                equity_contributed=[0.0] * 60,
                tranches=[SHLTranche(name="bad")],
            )

    def test_validation_drawdown_sum(self):
        with pytest.raises(ValueError, match="drawdown_schedule sum"):
            Inputs(
                periods=60, start_year=2026, start_month=1,
                equity_contributed=[0.0] * 60,
                tranches=[SHLTranche(
                    name="bad", fixed_amount_DKKk=1000.0,
                    drawdown_schedule=[500.0] + [0.0] * 59,  # sums to 500 not 1000
                )],
            )


# ---------------------------------------------------------------------------
# PIK compounding frequency tests
# ---------------------------------------------------------------------------

def test_pik_freq1_identical_to_default():
    """freq=1 (monthly) gives identical results to the original default."""
    out_default = calculate(_make_inputs())
    out_freq1 = calculate(_make_inputs().model_copy(update={"pik_compounding_frequency": 1}))
    assert out_default.total_interest == pytest.approx(out_freq1.total_interest, rel=1e-10)
    assert out_default.final_balance == pytest.approx(out_freq1.final_balance, rel=1e-10)


def test_pik_freq6_lower_than_freq1():
    """Semi-annual compounding produces less PIK interest than monthly."""
    out_m = calculate(_make_inputs(periods=360, margin=0.08))
    inp_sa = _make_inputs(periods=360, margin=0.08).model_copy(update={"pik_compounding_frequency": 6})
    out_sa = calculate(inp_sa)
    assert out_sa.total_interest < out_m.total_interest
    # Peak balance (before final repayment) should be lower
    assert max(out_sa.closing_balance) < max(out_m.closing_balance)


def test_pik_freq12_lower_than_freq6():
    """Annual compounding produces less interest than semi-annual."""
    inp6 = _make_inputs(periods=360, margin=0.08).model_copy(update={"pik_compounding_frequency": 6})
    inp12 = _make_inputs(periods=360, margin=0.08).model_copy(update={"pik_compounding_frequency": 12})
    out6 = calculate(inp6)
    out12 = calculate(inp12)
    assert out12.total_interest < out6.total_interest


def test_pik_freq_quantitative():
    """At 8% on 10,000 over 360m: monthly vs semi-annual ~2-3% difference."""
    n = 360
    eq = [10_000.0] + [0.0] * (n - 1)
    inp_m = Inputs(periods=n, start_year=2025, start_month=1,
                   shl_pct_of_equity=1.0, margin=0.08, accrued=True,
                   equity_contributed=eq)
    inp_sa = inp_m.model_copy(update={"pik_compounding_frequency": 6})
    out_m = calculate(inp_m)
    out_sa = calculate(inp_sa)
    # Compare peak balances (before final repayment in last period)
    peak_m = max(out_m.closing_balance[:-1])
    peak_sa = max(out_sa.closing_balance[:-1])
    diff_pct = (peak_m - peak_sa) / peak_m
    assert 0.01 < diff_pct < 0.10
