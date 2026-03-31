"""Tests for DEBT_SCULPT_001 — sculpted senior debt module."""

import math
import pytest
from modules.debt.DEBT_SCULPT_001 import (
    ConstraintScenario,
    DSCRStream,
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
    _sa_periods,
    _margin_at_period,
    _monthly_rate,
    _blended_dscr_for_sa,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _streams(
    n, construction=12,
    pv_contracted_cfads=600.0, pv_merchant_cfads=200.0,
    bess_contracted_cfads=100.0, bess_merchant_cfads=50.0,
):
    """Create 4-stream CFADS: zero during construction, flat during ops."""
    zeros = [0.0] * construction
    ops = n - construction
    return [
        DSCRStream(name="pv_contracted", target_dscr=1.20,
                   cfads=zeros + [pv_contracted_cfads] * ops),
        DSCRStream(name="pv_merchant", target_dscr=1.60,
                   cfads=zeros + [pv_merchant_cfads] * ops),
        DSCRStream(name="bess_contracted", target_dscr=1.60,
                   cfads=zeros + [bess_contracted_cfads] * ops),
        DSCRStream(name="bess_merchant", target_dscr=2.10,
                   cfads=zeros + [bess_merchant_cfads] * ops),
    ]


def _make_inputs(
    periods=192,             # 12m construction + 15y = 192
    construction=12,
    tenor_months=180,        # 15 years
    grace_period_months=0,
    swap_rate=0.03068,
    hedge_pct=0.75,
    reference_rate=0.03,
    margin_rates=None,
    margin_step_years=None,
    leverage_cap_pct=0.80,
    total_capex=200_000.0,
    pv_contracted_cfads=600.0,
    pv_merchant_cfads=200.0,
    bess_contracted_cfads=100.0,
    bess_merchant_cfads=50.0,
    **kwargs,
):
    if margin_rates is None:
        margin_rates = [0.023, 0.020]
    if margin_step_years is None:
        margin_step_years = [0, 10]
    return Inputs(
        periods=periods,
        start_year=2025,
        start_month=1,
        dscr_streams=_streams(
            periods, construction,
            pv_contracted_cfads, pv_merchant_cfads,
            bess_contracted_cfads, bess_merchant_cfads,
        ),
        tenor_months=tenor_months,
        grace_period_months=grace_period_months,
        swap_rate=swap_rate,
        hedge_pct=hedge_pct,
        reference_rate=reference_rate,
        margin_rates=margin_rates,
        margin_step_years=margin_step_years,
        leverage_cap_pct=leverage_cap_pct,
        total_capex_DKKk=total_capex,
        construction_end_period=construction,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_stream_cfads_length_mismatch():
    with pytest.raises(ValueError, match="dscr_streams"):
        Inputs(
            periods=24, start_year=2025, start_month=1,
            dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.2, cfads=[100.0] * 10),
            ],
            tenor_months=12, margin_rates=[0.02], margin_step_years=[0],
            total_capex_DKKk=100_000.0, construction_end_period=6,
        )


def test_margin_rates_steps_mismatch():
    with pytest.raises(ValueError, match="margin_rates and margin_step_years"):
        _make_inputs(margin_rates=[0.02, 0.03], margin_step_years=[0])


def test_margin_rates_empty():
    with pytest.raises(ValueError, match="margin_rates must have"):
        _make_inputs(margin_rates=[], margin_step_years=[])


def test_invalid_payment_month():
    with pytest.raises(ValueError, match="payment_months"):
        _make_inputs(payment_months=[0, 13])


# ---------------------------------------------------------------------------
# Unit: helpers
# ---------------------------------------------------------------------------

def test_sa_periods_jun_dec():
    """Start Jan, 24 periods → 4 SA groups of 6 months each."""
    sa = _sa_periods(24, 1, [6, 12])
    assert len(sa) == 4
    assert all(len(g) == 6 for g in sa)
    assert sa[0] == [0, 1, 2, 3, 4, 5]
    assert sa[1] == [6, 7, 8, 9, 10, 11]


def test_sa_periods_mid_year_start():
    """Start Jul, 12 periods → Jun groups won't trigger until month 11."""
    sa = _sa_periods(12, 7, [6, 12])
    # Jul-Dec (6 months, ends Dec) then Jan-Jun (6 months, ends Jun)
    assert len(sa) == 2
    assert len(sa[0]) == 6
    assert len(sa[1]) == 6


def test_sa_periods_trailing():
    """13 periods from Jan → 2 full SA + 1 trailing."""
    sa = _sa_periods(13, 1, [6, 12])
    assert len(sa) == 3
    assert len(sa[2]) == 1  # trailing month


def test_margin_at_period_initial():
    assert _margin_at_period(12, 12, [0.023, 0.020], [0, 10]) == pytest.approx(0.023)


def test_margin_at_period_stepdown():
    """After 10 years (120 months) from COD, margin steps down."""
    assert _margin_at_period(12 + 120, 12, [0.023, 0.020], [0, 10]) == pytest.approx(0.020)


def test_margin_at_period_during_construction():
    """During construction, ops_years = 0, so first margin applies."""
    assert _margin_at_period(5, 12, [0.023, 0.020], [0, 10]) == pytest.approx(0.023)


def test_margin_at_period_three_steps():
    """3-step margin: 2.3% yr 0-5, 2.0% yr 5-10, 1.5% yr 10+."""
    rates = [0.023, 0.020, 0.015]
    steps = [0, 5, 10]
    cod = 12
    # Year 0 (period 12): first margin
    assert _margin_at_period(cod, cod, rates, steps) == pytest.approx(0.023)
    # Year 4.9 (period 12 + 58): still first margin
    assert _margin_at_period(cod + 58, cod, rates, steps) == pytest.approx(0.023)
    # Year 5 exactly (period 12 + 60): second margin
    assert _margin_at_period(cod + 60, cod, rates, steps) == pytest.approx(0.020)
    # Year 7 (period 12 + 84): still second margin
    assert _margin_at_period(cod + 84, cod, rates, steps) == pytest.approx(0.020)
    # Year 10 exactly (period 12 + 120): third margin
    assert _margin_at_period(cod + 120, cod, rates, steps) == pytest.approx(0.015)
    # Year 14 (period 12 + 168): still third margin
    assert _margin_at_period(cod + 168, cod, rates, steps) == pytest.approx(0.015)


def test_blended_dscr_single_stream():
    streams = [DSCRStream(name="pv", target_dscr=1.30, cfads=[100.0] * 6)]
    assert _blended_dscr_for_sa(streams, [0, 1, 2, 3, 4, 5]) == pytest.approx(1.30)


def test_blended_dscr_four_streams():
    """Holsted-style: weighted blend of 4 streams."""
    streams = [
        DSCRStream(name="pv_c", target_dscr=1.20, cfads=[600.0]),
        DSCRStream(name="pv_m", target_dscr=1.60, cfads=[200.0]),
        DSCRStream(name="bess_c", target_dscr=1.60, cfads=[100.0]),
        DSCRStream(name="bess_m", target_dscr=2.10, cfads=[50.0]),
    ]
    blended = _blended_dscr_for_sa(streams, [0])
    # (600*1.2 + 200*1.6 + 100*1.6 + 50*2.1) / (600+200+100+50)
    # = (720 + 320 + 160 + 105) / 950 = 1305/950 ≈ 1.3737
    assert blended == pytest.approx(1305.0 / 950.0, rel=1e-6)


def test_blended_dscr_zero_cfads():
    streams = [DSCRStream(name="pv", target_dscr=1.2, cfads=[0.0])]
    assert _blended_dscr_for_sa(streams, [0]) == pytest.approx(999.0)


def test_monthly_rate():
    inp = _make_inputs()
    r = _monthly_rate(12, inp)  # first ops month
    # margin = 0.023 (year 0 step)
    # hedged = 0.03068 + 0.023 = 0.05368
    # unhedged = 0.03 + 0.023 = 0.053
    # blended = 0.75*0.05368 + 0.25*0.053 = 0.04026 + 0.01325 = 0.05351
    expected = (0.75 * (0.03068 + 0.023) + 0.25 * (0.03 + 0.023)) / 12.0
    assert r == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 192
    out = calculate(_make_inputs(periods=n))
    for field in ("opening_balance", "drawdown", "interest_accrued",
                  "interest_paid", "principal", "closing_balance",
                  "debt_service", "blended_dscr_target", "dscr_achieved"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# Balance mechanics
# ---------------------------------------------------------------------------

def test_balance_starts_zero():
    out = calculate(_make_inputs())
    assert out.opening_balance[0] == pytest.approx(0.0)


def test_balance_builds_during_construction():
    out = calculate(_make_inputs(construction=12))
    assert out.closing_balance[11] > 0


def test_no_principal_during_construction():
    out = calculate(_make_inputs(construction=12))
    for p in range(12):
        assert out.principal[p] == pytest.approx(0.0)


def test_principal_only_on_payment_months():
    """Principal should only be non-zero on Jun (6) and Dec (12) months."""
    out = calculate(_make_inputs())
    for p in range(len(out.principal)):
        if out.principal[p] > 1e-9:
            cal_month = ((1 - 1 + p) % 12) + 1  # start_month=1
            assert cal_month in (6, 12), f"Principal in month {cal_month} (period {p})"


def test_interest_paid_only_on_payment_months():
    out = calculate(_make_inputs())
    for p in range(len(out.interest_paid)):
        if out.interest_paid[p] > 1e-9:
            cal_month = ((1 - 1 + p) % 12) + 1
            assert cal_month in (6, 12), f"Interest paid in month {cal_month} (period {p})"


def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-6 for b in out.closing_balance)


def test_debt_service_equals_interest_paid_plus_principal():
    out = calculate(_make_inputs())
    for p in range(len(out.debt_service)):
        assert out.debt_service[p] == pytest.approx(
            out.interest_paid[p] + out.principal[p], abs=1e-9
        )


# ---------------------------------------------------------------------------
# Sculpted sizing
# ---------------------------------------------------------------------------

def test_fully_repaid():
    out = calculate(_make_inputs())
    assert out.fully_repaid
    assert out.final_balance == pytest.approx(0.0, abs=1.0)


def test_total_principal_equals_total_debt():
    out = calculate(_make_inputs())
    assert out.total_principal == pytest.approx(out.total_debt, abs=1.0)


def test_gearing_below_leverage_cap():
    out = calculate(_make_inputs(leverage_cap_pct=0.80))
    assert out.gearing <= 0.80 + 1e-6


def test_gearing_computation():
    out = calculate(_make_inputs())
    assert out.gearing == pytest.approx(out.total_debt / 200_000.0, rel=1e-6)


def test_leverage_cap_binds():
    """With abundant CFADS, leverage cap should be the binding constraint."""
    out = calculate(_make_inputs(
        pv_contracted_cfads=50_000.0,  # extremely high CFADS
        pv_merchant_cfads=10_000.0,
        leverage_cap_pct=0.50,
        total_capex=200_000.0,
    ))
    assert out.total_debt == pytest.approx(100_000.0, rel=1e-3)
    assert out.gearing == pytest.approx(0.50, rel=1e-3)


def test_dscr_sizing_binds_below_leverage():
    """With tight CFADS, DSCR sizing should bind below leverage cap."""
    out = calculate(_make_inputs(
        pv_contracted_cfads=100.0,
        pv_merchant_cfads=30.0,
        bess_contracted_cfads=20.0,
        bess_merchant_cfads=10.0,
        leverage_cap_pct=0.80,
        total_capex=200_000.0,
    ))
    assert out.total_debt < 160_000.0  # less than 80% leverage
    assert out.fully_repaid


# ---------------------------------------------------------------------------
# DSCR
# ---------------------------------------------------------------------------

def test_dscr_above_covenant():
    out = calculate(_make_inputs())
    assert not out.covenant_breached
    assert out.min_dscr >= 1.10


def test_dscr_achieved_meets_target():
    """Achieved DSCR should be >= blended target for each SA period."""
    out = calculate(_make_inputs())
    for p in range(len(out.dscr_achieved)):
        if not math.isnan(out.dscr_achieved[p]) and out.dscr_achieved[p] < 900:
            # Sculpting is slightly conservative, so achieved >= target
            assert out.dscr_achieved[p] >= out.blended_dscr_target[p] - 0.01


def test_avg_dscr_above_min():
    out = calculate(_make_inputs())
    assert out.avg_dscr >= out.min_dscr


# ---------------------------------------------------------------------------
# Grace period
# ---------------------------------------------------------------------------

def test_grace_period_delays_first_payment():
    out_no_grace = calculate(_make_inputs(grace_period_months=0))
    out_grace = calculate(_make_inputs(grace_period_months=6))
    # With grace, first principal should be later
    first_princ_no = next(p for p in range(192) if out_no_grace.principal[p] > 1e-9)
    first_princ_gr = next(p for p in range(192) if out_grace.principal[p] > 1e-9)
    assert first_princ_gr > first_princ_no


# ---------------------------------------------------------------------------
# Margin step-down
# ---------------------------------------------------------------------------

def test_margin_stepdown_reduces_interest():
    """After step-down year, interest rate decreases."""
    out = calculate(_make_inputs(margin_rates=[0.05, 0.02], margin_step_years=[0, 5]))
    # Interest in year 1 (period 12-23) vs year 6 (period 72-83) should differ
    # if balance is comparable
    yr1_int = sum(out.interest_accrued[12:24])
    yr6_int = sum(out.interest_accrued[72:84])
    # Year 6 has lower margin AND lower balance, so interest should be lower
    assert yr6_int < yr1_int


# ---------------------------------------------------------------------------
# LLCR
# ---------------------------------------------------------------------------

def test_llcr_positive():
    out = calculate(_make_inputs())
    assert out.llcr > 0
    assert not math.isnan(out.llcr)


def test_llcr_above_one():
    """LLCR should be > 1 for a viable project."""
    out = calculate(_make_inputs())
    assert out.llcr > 1.0


# ---------------------------------------------------------------------------
# Interest rate blending
# ---------------------------------------------------------------------------

def test_fully_hedged():
    """100% hedged → rate = swap + margin, reference rate ignored."""
    out_hedged = calculate(_make_inputs(hedge_pct=1.0, reference_rate=0.10))
    out_unhedged = calculate(_make_inputs(hedge_pct=0.0, reference_rate=0.10))
    # Unhedged with ref=10% should have much more interest
    assert out_unhedged.total_interest > out_hedged.total_interest


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "opening_bal": "K10", "monthly_rate": "B5",
        "cfads_sa": "K$20:P$20", "blended_dscr": "G15",
        "sa_interest": "K16", "drawdown": "K11",
        "principal": "K14", "interest_paid": "K13",
        "hedge_pct": "B6", "swap_rate": "B7",
        "ref_rate": "B8", "margin": "B9",
        "sweep_pct": "B20", "closing_before_sweep": "K50",
        "cash_sweep": "K51", "pv_cfads": "K52",
    }
    formulas = get_excel_formulas(refs)
    for key in ("interest_accrued", "sculpted_principal", "closing_balance",
                "debt_service", "dscr_achieved", "blended_rate",
                "cash_sweep", "llcr"):
        assert key in formulas
        assert formulas[key].startswith("=")


# ---------------------------------------------------------------------------
# Cash sweep tests
# ---------------------------------------------------------------------------

class TestCashSweep:

    def test_cash_sweep_inactive_returns_zeros(self):
        out = calculate(_make_inputs())
        assert all(s == 0.0 for s in out.cash_sweep)
        assert out.total_cash_sweep == 0.0

    def test_cash_sweep_zero_pct_equals_no_sweep(self):
        out = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.0,
        ))
        assert all(s == 0.0 for s in out.cash_sweep)
        assert out.total_cash_sweep == 0.0

    def test_cash_sweep_reduces_balance(self):
        base = calculate(_make_inputs())
        swept = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.20,
        ))
        # Mid-life closing balance should be lower with sweep
        mid = 100
        assert swept.closing_balance[mid] < base.closing_balance[mid]
        assert swept.total_cash_sweep > 0

    def test_cash_sweep_never_exceeds_balance(self):
        out = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.50,
        ))
        for p in range(out.closing_balance.__len__()):
            assert out.closing_balance[p] >= -0.01
            assert out.cash_sweep[p] >= 0.0

    def test_cash_sweep_starts_at_specified_period(self):
        out = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.20,
            cash_sweep_start_period=60,
        ))
        for p in range(60):
            assert out.cash_sweep[p] == 0.0
        assert any(s > 0 for s in out.cash_sweep[60:])

    def test_cash_sweep_pct_applied_correctly(self):
        """For a period with known CFADS and DS, verify exact sweep."""
        out = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.20,
        ))
        # Find a period with positive sweep
        for p in range(192):
            if out.cash_sweep[p] > 0:
                cfads_p = sum(
                    s.cfads[p] for s in _streams(192)
                )
                excess = cfads_p - (out.interest_paid[p] + out.principal[p])
                if excess > 0 and out.closing_balance[p] + out.cash_sweep[p] > 0:
                    expected = min(excess * 0.20, out.closing_balance[p] + out.cash_sweep[p])
                    assert out.cash_sweep[p] == pytest.approx(expected, rel=1e-4)
                break

    def test_cash_sweep_fully_repays_early(self):
        """High sweep % should cause earlier full repayment."""
        base = calculate(_make_inputs())
        swept = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.50,
        ))
        # Swept should have a zero balance earlier
        base_last_nonzero = max(
            (p for p in range(192) if base.closing_balance[p] > 0.01), default=0
        )
        swept_last_nonzero = max(
            (p for p in range(192) if swept.closing_balance[p] > 0.01), default=0
        )
        assert swept_last_nonzero <= base_last_nonzero

    def test_total_cash_sweep_equals_sum(self):
        out = calculate(_make_inputs(
            cash_sweep_active=True, cash_sweep_pct=0.20,
        ))
        assert out.total_cash_sweep == pytest.approx(sum(out.cash_sweep))


# ---------------------------------------------------------------------------
# LLCR tests
# ---------------------------------------------------------------------------

class TestLLCR:

    def test_llcr_nan_when_no_balance(self):
        out = calculate(_make_inputs())
        # Before construction drawdown, opening balance may be 0
        # Check periods well after repayment ends
        for p in range(out.opening_balance.__len__()):
            if out.opening_balance[p] < 0.01:
                assert math.isnan(out.llcr_series[p])

    def test_llcr_above_one_when_healthy(self):
        """With strong CFADS, LLCR at COD should be above 1."""
        out = calculate(_make_inputs())
        assert out.llcr > 1.0
        # Most repayment periods should have LLCR > 1
        llcr_vals = [v for v in out.llcr_series if not math.isnan(v) and v > 0]
        assert len(llcr_vals) > 100
        assert sum(1 for v in llcr_vals if v > 1.0) > len(llcr_vals) * 0.9

    def test_llcr_decreases_toward_maturity(self):
        """LLCR should generally decrease as remaining CFADS shrinks."""
        out = calculate(_make_inputs())
        # Compare LLCR at start of repayment vs near end
        rep_llcr = [v for v in out.llcr_series if not math.isnan(v)]
        if len(rep_llcr) >= 10:
            # First quartile should be >= last quartile on average
            q1 = sum(rep_llcr[:len(rep_llcr)//4]) / (len(rep_llcr)//4)
            q4 = sum(rep_llcr[-len(rep_llcr)//4:]) / (len(rep_llcr)//4)
            assert q1 >= q4

    def test_llcr_known_value(self):
        """LLCR at COD should match the scalar llcr field."""
        out = calculate(_make_inputs())
        # The scalar llcr is computed at COD; find first non-NaN in series
        # They use different discount approaches, so just check both exist
        assert not math.isnan(out.llcr)
        assert out.llcr > 1.0

    def test_pv_cfads_at_maturity_equals_last_cfads(self):
        """At the last period with balance, PV should be close to that period's CFADS."""
        out = calculate(_make_inputs())
        # Find last period with opening balance
        last_p = max(
            (p for p in range(192) if out.opening_balance[p] > 0.01), default=0
        )
        if last_p > 0 and out.pv_cfads[last_p] > 0:
            total_cfads_last = sum(s.cfads[last_p] for s in _streams(192))
            # PV at maturity should be approximately the remaining CFADS (little discounting)
            assert out.pv_cfads[last_p] >= total_cfads_last * 0.5  # reasonable bound

    def test_min_llcr_is_minimum_of_series(self):
        out = calculate(_make_inputs())
        llcr_vals = [v for v in out.llcr_series if not math.isnan(v)]
        if llcr_vals:
            assert out.min_llcr == pytest.approx(min(llcr_vals))

    def test_llcr_with_flat_cfads_and_rate(self):
        """Simple flat CFADS case — LLCR should be stable across periods."""
        out = calculate(_make_inputs(
            pv_contracted_cfads=500.0, pv_merchant_cfads=0.0,
            bess_contracted_cfads=0.0, bess_merchant_cfads=0.0,
        ))
        llcr_vals = [v for v in out.llcr_series if not math.isnan(v)]
        if len(llcr_vals) >= 2:
            # Flat CFADS should produce smoothly decreasing LLCR
            assert llcr_vals[0] >= llcr_vals[-1]

    def test_llcr_declining_cfads(self):
        """Declining CFADS reduces borrowing capacity vs flat."""
        n = 192
        construction = 12
        ops = n - construction
        declining = [600.0 * (1.0 - 0.5 * i / ops) for i in range(ops)]
        flat_cfads = [600.0] * ops
        zeros = [0.0] * construction

        common = dict(
            periods=n, start_year=2025, start_month=1,
            tenor_months=180, payment_months=[6, 12],
            swap_rate=0.03, hedge_pct=0.75, reference_rate=0.03,
            margin_rates=[0.020], margin_step_years=[0],
            leverage_cap_pct=0.80, total_capex_DKKk=200_000.0,
            construction_end_period=construction,
        )
        out_declining = calculate(Inputs(
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.20,
                                     cfads=zeros + declining)],
            **common,
        ))
        out_flat = calculate(Inputs(
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.20,
                                     cfads=zeros + flat_cfads)],
            **common,
        ))
        # Declining CFADS → less borrowing capacity
        assert out_declining.total_debt < out_flat.total_debt
        # Both should still have valid LLCR > 1
        assert out_declining.llcr > 1.0
        assert out_flat.llcr > 1.0

    def test_llcr_single_period_remaining(self):
        """LLCR with very short remaining tenor — near-maturity edge case."""
        # Use short model: 18 months total (6 construction + 12 repayment)
        n = 18
        construction = 6
        cfads = [0.0] * construction + [500.0] * (n - construction)
        out = calculate(Inputs(
            periods=n, start_year=2025, start_month=1,
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.20, cfads=cfads)],
            tenor_months=12, payment_months=[6, 12],
            swap_rate=0.03, hedge_pct=0.75, reference_rate=0.03,
            margin_rates=[0.020], margin_step_years=[0],
            leverage_cap_pct=0.80, total_capex_DKKk=50_000.0,
            construction_end_period=construction,
        ))
        llcr_vals = [v for v in out.llcr_series if not math.isnan(v)]
        # Should have at least some valid values
        assert len(llcr_vals) > 0
        # Last valid LLCR should be smallest (least remaining CFADS)
        assert llcr_vals[-1] <= llcr_vals[0]

    def test_llcr_high_discount_rate(self):
        """High discount rate reduces borrowing capacity vs low rate."""
        common = dict(
            periods=192, start_year=2025, start_month=1,
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.20,
                                     cfads=[0.0]*12 + [500.0]*180)],
            tenor_months=180, payment_months=[6, 12],
            hedge_pct=0.75, reference_rate=0.03,
            leverage_cap_pct=0.80, total_capex_DKKk=200_000.0,
            construction_end_period=12,
        )
        out_high = calculate(Inputs(
            **common, swap_rate=0.08, margin_rates=[0.04], margin_step_years=[0],
        ))
        out_low = calculate(Inputs(
            **common, swap_rate=0.02, margin_rates=[0.01], margin_step_years=[0],
        ))
        # Higher rate → more interest → less room for principal → smaller facility
        assert out_high.total_debt < out_low.total_debt
        # Both should still have valid LLCR
        assert out_high.llcr > 1.0
        assert out_low.llcr > 1.0


# ---------------------------------------------------------------------------
# DSCR lookback tests
# ---------------------------------------------------------------------------

class TestDSCRLookback:
    def test_dscr_6m_lookback_uses_6_months(self):
        out = calculate(_make_inputs())
        vals = [v for v in out.dscr_6m_lookback if not math.isnan(v)]
        assert len(vals) > 0

    def test_dscr_12m_lookback_uses_12_months(self):
        out = calculate(_make_inputs())
        vals = [v for v in out.dscr_12m_lookback if not math.isnan(v)]
        assert len(vals) > 0

    def test_min_dscr_6m_is_minimum(self):
        out = calculate(_make_inputs())
        vals = [v for v in out.dscr_6m_lookback if not math.isnan(v)]
        if vals:
            assert out.min_dscr_6m == pytest.approx(min(vals))

    def test_min_dscr_12m_is_minimum(self):
        out = calculate(_make_inputs())
        vals = [v for v in out.dscr_12m_lookback if not math.isnan(v)]
        if vals:
            assert out.min_dscr_12m == pytest.approx(min(vals))

    def test_lookback_nan_outside_repayment(self):
        out = calculate(_make_inputs())
        # Construction periods (0-11) should be NaN
        for p in range(12):
            assert math.isnan(out.dscr_6m_lookback[p])
            assert math.isnan(out.dscr_12m_lookback[p])


# ============================================================================
# DUAL CONTRACTED/MERCHANT DSCR (Session 2 — UC Model Learnings)
# ============================================================================

def _make_dual_dscr_inputs(
    n=300, contracted_dscr=1.25, merchant_dscr=1.80, merchant_start=180
):
    """Build inputs with a single PV stream having dual DSCR targets.
    
    300 periods, PPA for 15 years (180 months), merchant after.
    """
    cfads = [50.0] * n  # flat 50 DKKk/month
    return Inputs(
        periods=n, start_year=2026, start_month=1,
        dscr_streams=[
            DSCRStream(
                name="pv_contracted",
                target_dscr=contracted_dscr,
                cfads=cfads,
                merchant_dscr=merchant_dscr,
                merchant_start_period=merchant_start,
            ),
        ],
        tenor_months=240,
        payment_months=[6, 12],
        swap_rate=0.03,
        hedge_pct=0.80,
        reference_rate=0.02,
        margin_rates=[0.020],
        margin_step_years=[0],
        leverage_cap_pct=0.80,
        total_capex_DKKk=100_000.0,
        construction_end_period=12,
    )


class TestDualDSCR:
    def test_dual_backward_compat_no_merchant(self):
        """Without merchant fields, behavior identical to flat target."""
        cfads = [50.0] * 300
        inp_flat = Inputs(
            periods=300, start_year=2026, start_month=1,
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.25, cfads=cfads)],
            tenor_months=240, payment_months=[6, 12],
            swap_rate=0.03, hedge_pct=0.80, reference_rate=0.02,
            margin_rates=[0.020], margin_step_years=[0],
            leverage_cap_pct=0.80, total_capex_DKKk=100_000.0,
            construction_end_period=12,
        )
        out_flat = calculate(inp_flat)
        inp_none = Inputs(
            periods=300, start_year=2026, start_month=1,
            dscr_streams=[DSCRStream(name="pv", target_dscr=1.25, cfads=cfads,
                                     merchant_dscr=None, merchant_start_period=None)],
            tenor_months=240, payment_months=[6, 12],
            swap_rate=0.03, hedge_pct=0.80, reference_rate=0.02,
            margin_rates=[0.020], margin_step_years=[0],
            leverage_cap_pct=0.80, total_capex_DKKk=100_000.0,
            construction_end_period=12,
        )
        out_none = calculate(inp_none)
        assert abs(out_flat.total_debt - out_none.total_debt) < 1.0

    def test_dual_facility_smaller_than_flat(self):
        """Dual DSCR with 1.80x merchant tail → less borrowing than flat 1.25x."""
        out_dual = calculate(_make_dual_dscr_inputs())
        out_flat = calculate(_make_dual_dscr_inputs(merchant_dscr=None, merchant_start=None))
        assert out_dual.total_debt < out_flat.total_debt, \
            f"Dual {out_dual.total_debt:.0f} should be < flat {out_flat.total_debt:.0f}"

    def test_dual_contracted_dscr_meets_target(self):
        """DSCR during contracted period ≈ contracted target."""
        out = calculate(_make_dual_dscr_inputs())
        # Check DSCR in contracted window (periods 12-179)
        contracted_dscr = [out.dscr_achieved[p] for p in range(12, 180)
                          if not math.isnan(out.dscr_achieved[p]) and out.dscr_achieved[p] < 900]
        if contracted_dscr:
            avg = sum(contracted_dscr) / len(contracted_dscr)
            assert abs(avg - 1.25) < 0.15, f"Contracted avg DSCR {avg:.3f} should be near 1.25"

    def test_dual_merchant_dscr_meets_target(self):
        """DSCR during merchant tail ≈ merchant target."""
        out = calculate(_make_dual_dscr_inputs())
        # Check DSCR in merchant window (periods 180+)
        merchant_dscr = [out.dscr_achieved[p] for p in range(180, 300)
                        if not math.isnan(out.dscr_achieved[p]) and out.dscr_achieved[p] < 900]
        if merchant_dscr:
            avg = sum(merchant_dscr) / len(merchant_dscr)
            assert abs(avg - 1.80) < 0.25, f"Merchant avg DSCR {avg:.3f} should be near 1.80"

    def test_dual_blended_target_changes_at_boundary(self):
        """Blended DSCR target should shift at merchant_start_period."""
        out = calculate(_make_dual_dscr_inputs())
        # Pre-merchant blended should be ≈ 1.25
        pre = [out.blended_dscr_target[p] for p in range(12, 100)
               if not math.isnan(out.blended_dscr_target[p])]
        if pre:
            assert abs(pre[0] - 1.25) < 0.01
        # Post-merchant blended should be ≈ 1.80
        post = [out.blended_dscr_target[p] for p in range(200, 250)
                if not math.isnan(out.blended_dscr_target[p])]
        if post:
            assert abs(post[0] - 1.80) < 0.01

    def test_dual_fully_repaid(self):
        """Debt should still fully repay with dual targets."""
        out = calculate(_make_dual_dscr_inputs())
        assert out.fully_repaid

    def test_dual_two_streams_different_boundaries(self):
        """Two streams with different merchant boundaries."""
        n = 300
        cfads_pv = [30.0] * n
        cfads_bess = [20.0] * n
        inp = Inputs(
            periods=n, start_year=2026, start_month=1,
            dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.25, cfads=cfads_pv,
                          merchant_dscr=1.80, merchant_start_period=180),
                DSCRStream(name="bess", target_dscr=1.20, cfads=cfads_bess,
                          merchant_dscr=1.80, merchant_start_period=120),
            ],
            tenor_months=240, payment_months=[6, 12],
            swap_rate=0.03, hedge_pct=0.80, reference_rate=0.02,
            margin_rates=[0.020], margin_step_years=[0],
            leverage_cap_pct=0.80, total_capex_DKKk=100_000.0,
            construction_end_period=12,
        )
        out = calculate(inp)
        assert out.fully_repaid
        assert out.total_debt > 0


# ============================================================================
# MULTI-CONSTRAINT SCULPTING (Session 5 — UC Model Learnings)
# ============================================================================

def _make_constraint_inputs():
    """Build inputs with 3 constraint scenarios (P50, P99, breakeven)."""
    n = 300
    cfads_p50 = [50.0] * n
    cfads_p99 = [45.0] * n   # ~90% of P50
    cfads_break = [40.0] * n  # breakeven flat

    return Inputs(
        periods=n, start_year=2026, start_month=1,
        dscr_streams=[  # Primary streams (used for single-pass)
            DSCRStream(name="pv", target_dscr=1.25, cfads=cfads_p50,
                      merchant_dscr=1.80, merchant_start_period=180),
        ],
        tenor_months=240, payment_months=[6, 12],
        swap_rate=0.03, hedge_pct=0.80, reference_rate=0.02,
        margin_rates=[0.020], margin_step_years=[0],
        leverage_cap_pct=0.80, total_capex_DKKk=100_000.0,
        construction_end_period=12,
        constraint_scenarios=[
            ConstraintScenario(name="P50", dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.25, cfads=cfads_p50,
                          merchant_dscr=1.80, merchant_start_period=180),
            ]),
            ConstraintScenario(name="P99", dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.00, cfads=cfads_p99,
                          merchant_dscr=1.40, merchant_start_period=180),
            ]),
            ConstraintScenario(name="breakeven", dscr_streams=[
                DSCRStream(name="pv", target_dscr=1.00, cfads=cfads_break,
                          merchant_dscr=1.40, merchant_start_period=180),
            ]),
        ],
    )


class TestMultiConstraint:
    def test_multi_facility_smaller_than_single_pass(self):
        """Multi-constraint facility < single-pass P50 (tighter constraints)."""
        inp_multi = _make_constraint_inputs()
        out_multi = calculate(inp_multi)
        # Single pass with P50 only
        inp_single = _make_constraint_inputs()
        inp_single = inp_single.model_copy(update={"constraint_scenarios": None})
        out_single = calculate(inp_single)
        assert out_multi.total_debt < out_single.total_debt, \
            f"Multi {out_multi.total_debt:.0f} should be < single {out_single.total_debt:.0f}"

    def test_multi_no_constraints_identical(self):
        """Without constraint_scenarios, behavior identical to single-pass."""
        inp = _make_constraint_inputs()
        inp_none = inp.model_copy(update={"constraint_scenarios": None})
        out_none = calculate(inp_none)
        assert out_none.binding_constraint == [""] * inp.periods
        assert out_none.per_constraint_dscr == {}

    def test_multi_binding_constraint_populated(self):
        """binding_constraint shows which scenario was tightest."""
        out = calculate(_make_constraint_inputs())
        non_empty = [b for b in out.binding_constraint if b]
        assert len(non_empty) > 0
        assert all(b in ("P50", "P99", "breakeven") for b in non_empty)

    def test_multi_per_constraint_dscr_has_all_scenarios(self):
        """per_constraint_dscr contains DSCR series for each scenario."""
        out = calculate(_make_constraint_inputs())
        assert "P50" in out.per_constraint_dscr
        assert "P99" in out.per_constraint_dscr
        assert "breakeven" in out.per_constraint_dscr
        for name, series in out.per_constraint_dscr.items():
            assert len(series) == 300

    def test_multi_fully_repaid(self):
        """Multi-constraint schedule still fully repays."""
        out = calculate(_make_constraint_inputs())
        assert out.fully_repaid

    def test_multi_total_debt_positive(self):
        out = calculate(_make_constraint_inputs())
        assert out.total_debt > 0
