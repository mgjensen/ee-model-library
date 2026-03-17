"""
Tests for DEBT_001 new features:
  - Margin step-ups (GAP 1)
  - Cash sweep (GAP 3)
"""

import math
import pytest
from modules.debt.DEBT_001 import (
    DSCRStream,
    MarginStep,
    Inputs,
    Outputs,
    calculate,
    _monthly_rates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _even_drawdowns(facility, n_draw, total_periods):
    d = [facility / n_draw] * n_draw + [0.0] * (total_periods - n_draw)
    return d


# ---------------------------------------------------------------------------
# GAP 1: Margin step-ups
# ---------------------------------------------------------------------------

class TestMarginStepUps:

    def test_flat_rate_backward_compat(self):
        """No margin_schedule -> flat rate everywhere."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
        )
        rates = _monthly_rates(inp)
        assert all(r == pytest.approx(0.05 / 12) for r in rates)

    def test_single_step_matches_flat(self):
        """One margin step covering everything = same as flat base+margin."""
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,  # still required but overridden
            base_rate=0.02,
            margin_schedule=[MarginStep(margin=0.03, until_year=2050)],
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
        )
        rates = _monthly_rates(inp)
        assert all(r == pytest.approx(0.05 / 12) for r in rates)

    def test_three_step_holsted(self):
        """Holsted Hybrid: 2.3% -> 2.0% -> 1.5% with base ~0.768%."""
        base = 0.00768
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,  # fallback
            base_rate=base,
            margin_schedule=[
                MarginStep(margin=0.023, until_year=2036),
                MarginStep(margin=0.020, until_year=2041),
                MarginStep(margin=0.015, until_year=2060),
            ],
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 240),
            periods=240,
            start_year=2026,
            start_month=1,
        )
        rates = _monthly_rates(inp)
        # Period 0 = Jan 2026 -> year 2026 <= 2036 -> margin 2.3%
        assert rates[0] == pytest.approx((base + 0.023) / 12)
        # Period 120 = Jan 2036 -> year 2036 <= 2036 -> margin 2.3%
        assert rates[120] == pytest.approx((base + 0.023) / 12)
        # Period 132 = Jan 2037 -> year 2037 <= 2041 -> margin 2.0%
        assert rates[132] == pytest.approx((base + 0.020) / 12)
        # Period 192 = Jan 2042 -> year 2042 <= 2060 -> margin 1.5%
        assert rates[192] == pytest.approx((base + 0.015) / 12)

    def test_step_up_changes_total_interest(self):
        """Higher margins -> more interest vs flat rate."""
        base_inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            repayment_type="straight_line",
            tenor_months=120,
            drawdowns=_even_drawdowns(100_000, 12, 180),
            periods=180,
            start_year=2026,
            start_month=1,
        )
        stepped_inp = base_inp.model_copy(update={
            "base_rate": 0.02,
            "margin_schedule": [
                MarginStep(margin=0.04, until_year=2031),  # higher early
                MarginStep(margin=0.02, until_year=2060),  # lower late
            ],
        })
        out_flat = calculate(base_inp)
        out_stepped = calculate(stepped_inp)
        # The stepped version has higher early interest
        assert out_stepped.total_interest != pytest.approx(out_flat.total_interest, abs=100)

    def test_validation_margin_without_base_rate(self):
        with pytest.raises(ValueError, match="margin_schedule requires base_rate"):
            Inputs(
                facility=100_000.0,
                all_in_rate=0.05,
                margin_schedule=[MarginStep(margin=0.02, until_year=2030)],
                repayment_type="straight_line",
                tenor_months=120,
                drawdowns=_even_drawdowns(100_000, 12, 180),
                periods=180,
                start_year=2026,
                start_month=1,
            )

    def test_validation_empty_margin_schedule(self):
        with pytest.raises(ValueError, match="margin_schedule must not be empty"):
            Inputs(
                facility=100_000.0,
                all_in_rate=0.05,
                base_rate=0.02,
                margin_schedule=[],
                repayment_type="straight_line",
                tenor_months=120,
                drawdowns=_even_drawdowns(100_000, 12, 180),
                periods=180,
                start_year=2026,
                start_month=1,
            )

    def test_validation_non_chronological_steps(self):
        with pytest.raises(ValueError, match="must be > previous"):
            Inputs(
                facility=100_000.0,
                all_in_rate=0.05,
                base_rate=0.02,
                margin_schedule=[
                    MarginStep(margin=0.02, until_year=2035),
                    MarginStep(margin=0.03, until_year=2030),  # backward
                ],
                repayment_type="straight_line",
                tenor_months=120,
                drawdowns=_even_drawdowns(100_000, 12, 180),
                periods=180,
                start_year=2026,
                start_month=1,
            )

    def test_margin_step_works_with_sculpted(self):
        """Margin steps should work with sculpted debt too."""
        n = 180
        cfads = [0.0] * 12 + [2000.0] * 168
        inp = Inputs(
            facility=100_000.0,
            all_in_rate=0.05,
            base_rate=0.02,
            margin_schedule=[
                MarginStep(margin=0.025, until_year=2035),
                MarginStep(margin=0.020, until_year=2060),
            ],
            repayment_type="sculpted",
            tenor_months=168,
            drawdowns=_even_drawdowns(100_000, 12, n),
            periods=n,
            start_year=2026,
            start_month=1,
            sculpted_dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.4, cfads=cfads),
            ],
        )
        out = calculate(inp)
        assert out.sized_facility > 0
        assert out.total_interest > 0


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
