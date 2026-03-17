"""
Tests for DEBT_001 GAP 3: Cash sweep mandatory prepayment.
"""

import pytest
from modules.debt.DEBT_001 import Inputs, Outputs, calculate, DSCRStream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _even_drawdowns(facility, n_draw, total_periods):
    d = [facility / n_draw] * n_draw + [0.0] * (total_periods - n_draw)
    return d


# ---------------------------------------------------------------------------
# GAP 3: Cash sweep
# ---------------------------------------------------------------------------

class TestCashSweep:

    def test_sweep_zero_by_default(self):
        """cash_sweep_pct=0 -> all zeros, total_cash_sweep=0."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
            cfads=[0.0] * 12 + [2000.0] * 168,
        )
        out = calculate(inp)
        assert all(s == 0.0 for s in out.cash_sweep)
        assert out.total_cash_sweep == 0.0

    def test_sweep_reduces_balance(self):
        """20% sweep should reduce closing balance faster."""
        base = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
            cfads=[0.0] * 12 + [2000.0] * 168,
        )
        swept = base.model_copy(update={"cash_sweep_pct": 0.20})
        out_base = calculate(base)
        out_swept = calculate(swept)
        # Swept closing balance should be lower at midpoint
        mid = 90
        assert out_swept.closing_balance[mid] < out_base.closing_balance[mid]
        assert out_swept.total_cash_sweep > 0

    def test_sweep_only_after_start_period(self):
        """Sweep starts at specified period, not before."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
            cfads=[0.0] * 12 + [2000.0] * 168,
            cash_sweep_pct=0.20,
            cash_sweep_start_period=24,
        )
        out = calculate(inp)
        for p in range(24):
            assert out.cash_sweep[p] == 0.0
        # After period 24, some sweep should occur
        assert any(s > 0 for s in out.cash_sweep[24:])

    def test_sweep_capped_at_closing_balance(self):
        """Very high CFADS + 50% sweep -> closing never negative."""
        inp = Inputs(
            facility=10_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=60,
            drawdowns=_even_drawdowns(10_000, 6, 120),
            periods=120,
            start_year=2026,
            start_month=1,
            cfads=[0.0] * 6 + [5000.0] * 114,  # very high CFADS
            cash_sweep_pct=0.50,
        )
        out = calculate(inp)
        for p in range(120):
            assert out.cash_sweep[p] >= 0
            # closing should never go negative
            assert out.closing_balance[p] >= -0.01

    def test_sweep_no_cfads_no_sweep(self):
        """Non-sculpted with no CFADS -> sweep stays zero even if pct > 0."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
            cash_sweep_pct=0.20,
            # cfads not provided
        )
        out = calculate(inp)
        assert all(s == 0.0 for s in out.cash_sweep)

    def test_sweep_included_in_debt_service(self):
        """debt_service = interest + principal + cash_sweep."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
            cfads=[0.0] * 12 + [2000.0] * 168,
            cash_sweep_pct=0.20,
        )
        out = calculate(inp)
        for p in range(180):
            expected_ds = out.interest[p] + out.principal[p] + out.cash_sweep[p]
            assert out.debt_service[p] == pytest.approx(expected_ds, abs=0.01)
