"""Tests for VAT_FACILITY_001 — construction VAT financing facility."""

import pytest
from modules.debt.VAT_FACILITY_001 import (
    Inputs,
    Outputs,
    calculate,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=24,
    vat_rate=0.25,
    delay=1,
    margin=0.05,
    commitment_fee_rate=0.005,
    capex=None,
    construction_months=12,
):
    if capex is None:
        capex = [10_000.0] * construction_months + [0.0] * (periods - construction_months)
    return Inputs(
        periods=periods,
        start_year=2025,
        start_month=1,
        vat_rate=vat_rate,
        reimbursement_delay_months=delay,
        margin=margin,
        commitment_fee_rate=commitment_fee_rate,
        capex_monthly=capex,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_capex_length_mismatch():
    with pytest.raises(ValueError, match="capex_monthly"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            capex_monthly=[100.0] * 6,
        )


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_returns_outputs():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_array_lengths():
    n = 24
    out = calculate(_make_inputs(periods=n))
    for field in ("vat_paid", "vat_refund", "facility_balance",
                  "interest", "commitment_fee_paid"):
        assert len(getattr(out, field)) == n, f"{field} wrong length"


# ---------------------------------------------------------------------------
# VAT paid
# ---------------------------------------------------------------------------

def test_vat_paid_is_capex_times_rate():
    rate = 0.25
    out = calculate(_make_inputs(vat_rate=rate))
    for p in range(24):
        assert out.vat_paid[p] == pytest.approx(
            _make_inputs().capex_monthly[p] * rate
        )


def test_vat_paid_zero_when_no_capex():
    out = calculate(_make_inputs(capex=[0.0] * 24))
    assert all(v == pytest.approx(0.0) for v in out.vat_paid)


def test_total_vat_paid():
    out = calculate(_make_inputs())
    assert out.total_vat_paid == pytest.approx(sum(out.vat_paid))


# ---------------------------------------------------------------------------
# VAT refund
# ---------------------------------------------------------------------------

def test_refund_delayed_by_one_month():
    out = calculate(_make_inputs(delay=1))
    # Period 0: no refund (nothing to refund yet)
    assert out.vat_refund[0] == pytest.approx(0.0)
    # Period 1: refund of period 0 VAT
    assert out.vat_refund[1] == pytest.approx(out.vat_paid[0])


def test_refund_delayed_by_three_months():
    out = calculate(_make_inputs(delay=3))
    assert out.vat_refund[0] == pytest.approx(0.0)
    assert out.vat_refund[1] == pytest.approx(0.0)
    assert out.vat_refund[2] == pytest.approx(0.0)
    assert out.vat_refund[3] == pytest.approx(out.vat_paid[0])


def test_refund_zero_delay():
    """Delay=0 means refund in the same period as payment."""
    out = calculate(_make_inputs(delay=0))
    for p in range(24):
        assert out.vat_refund[p] == pytest.approx(out.vat_paid[p])


def test_total_refund_equals_total_paid_when_enough_periods():
    """With enough trailing periods, all VAT is eventually refunded."""
    out = calculate(_make_inputs(periods=36, delay=1, construction_months=12))
    assert out.total_vat_refund == pytest.approx(out.total_vat_paid)


def test_refund_less_than_paid_when_cutoff():
    """If model ends before last refund, total refund < total paid."""
    out = calculate(_make_inputs(periods=12, delay=1, construction_months=12))
    # 12 months of capex, 1 month delay: refunds for periods 0-10 arrive,
    # but period 11 refund would be in period 12 which doesn't exist
    assert out.total_vat_refund < out.total_vat_paid


# ---------------------------------------------------------------------------
# Facility balance
# ---------------------------------------------------------------------------

def test_balance_zero_with_no_delay():
    """No delay → refund = payment → balance always 0."""
    out = calculate(_make_inputs(delay=0))
    for p in range(24):
        assert out.facility_balance[p] == pytest.approx(0.0)


def test_balance_positive_during_construction():
    out = calculate(_make_inputs(delay=1))
    # Period 0: paid VAT but no refund yet
    assert out.facility_balance[0] > 0


def test_balance_equals_cumulative_paid_minus_refund():
    out = calculate(_make_inputs(delay=2))
    cum_paid = 0.0
    cum_refund = 0.0
    for p in range(24):
        cum_paid += out.vat_paid[p]
        cum_refund += out.vat_refund[p]
        assert out.facility_balance[p] == pytest.approx(cum_paid - cum_refund)


def test_balance_returns_to_zero_after_construction():
    """After last capex + delay, balance should return to zero."""
    out = calculate(_make_inputs(periods=36, delay=1, construction_months=12))
    # Period 13 onward: all VAT refunded, no new capex
    assert out.facility_balance[13] == pytest.approx(0.0)


def test_balance_never_negative():
    out = calculate(_make_inputs())
    assert all(b >= -1e-9 for b in out.facility_balance)


def test_peak_balance():
    out = calculate(_make_inputs(delay=1))
    assert out.peak_balance == pytest.approx(max(out.facility_balance))
    assert out.peak_balance > 0


# ---------------------------------------------------------------------------
# Interest
# ---------------------------------------------------------------------------

def test_interest_on_balance():
    margin = 0.06
    out = calculate(_make_inputs(margin=margin))
    r = margin / 12.0
    for p in range(24):
        assert out.interest[p] == pytest.approx(out.facility_balance[p] * r)


def test_zero_margin_no_interest():
    out = calculate(_make_inputs(margin=0.0))
    assert all(v == pytest.approx(0.0) for v in out.interest)
    assert out.total_interest == pytest.approx(0.0)


def test_total_interest():
    out = calculate(_make_inputs(margin=0.05))
    assert out.total_interest == pytest.approx(sum(out.interest))


# ---------------------------------------------------------------------------
# Commitment fee
# ---------------------------------------------------------------------------

def test_commitment_fee_on_undrawn():
    rate = 0.012
    out = calculate(_make_inputs(commitment_fee_rate=rate, delay=1))
    r = rate / 12.0
    peak = out.peak_balance
    for p in range(24):
        expected = max(0.0, peak - out.facility_balance[p]) * r
        assert out.commitment_fee_paid[p] == pytest.approx(expected)


def test_zero_commitment_fee_rate():
    out = calculate(_make_inputs(commitment_fee_rate=0.0))
    assert all(v == pytest.approx(0.0) for v in out.commitment_fee_paid)


def test_commitment_fee_highest_when_balance_zero():
    """When balance=0 (post-construction), commitment fee is at peak × rate."""
    out = calculate(_make_inputs(
        periods=36, delay=1, construction_months=12, commitment_fee_rate=0.01
    ))
    # After balance returns to zero, fee = peak * monthly_rate
    if out.facility_balance[20] == pytest.approx(0.0, abs=0.01):
        r = 0.01 / 12.0
        assert out.commitment_fee_paid[20] == pytest.approx(out.peak_balance * r)


def test_total_commitment_fee():
    out = calculate(_make_inputs(commitment_fee_rate=0.005))
    assert out.total_commitment_fee == pytest.approx(sum(out.commitment_fee_paid))


# ---------------------------------------------------------------------------
# get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "capex": "K20", "vat_rate": "B5", "vat_paid_col": "K10",
        "delay": "B6", "cum_vat_paid": "K11", "cum_vat_refund": "K12",
        "facility_balance": "K13", "monthly_margin": "B7",
        "peak": "G5", "monthly_commit": "B8",
    }
    formulas = get_excel_formulas(refs)
    for key in ("vat_paid", "vat_refund", "facility_balance",
                "interest", "commitment_fee"):
        assert key in formulas
        assert formulas[key].startswith("=")
