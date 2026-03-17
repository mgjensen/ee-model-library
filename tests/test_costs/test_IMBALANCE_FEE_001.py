"""Tests for IMBALANCE_FEE_001 — polluter pays imbalance fee."""

import pytest
from modules.costs.IMBALANCE_FEE_001 import Inputs, Outputs, calculate


def _make_inputs(periods=120, production=1000.0, fee=5.0, imbalance_pct=0.05, **kwargs):
    defaults = dict(
        periods=periods, start_year=2026, start_month=1, active=True,
        fee_per_MWh=fee, production_MWh=[production] * periods,
        imbalance_pct=imbalance_pct, inflation_rate=0.025, inflation_start_year=2026,
    )
    defaults.update(kwargs)
    return Inputs(**defaults)


def test_basic_fee():
    out = calculate(_make_inputs())
    # 1000 MWh * 0.05 * 5.0 / 1000 = 0.25 DKKk (year 1, no inflation)
    assert out.imbalance_fee_monthly[0] == pytest.approx(0.25)


def test_inactive_zeros():
    out = calculate(_make_inputs(active=False))
    assert all(f == 0.0 for f in out.imbalance_fee_monthly)
    assert out.total_imbalance_fee == 0.0


def test_zero_fee_rate():
    out = calculate(_make_inputs(fee=0.0))
    assert out.total_imbalance_fee == 0.0


def test_zero_production():
    out = calculate(_make_inputs(production=0.0))
    assert out.total_imbalance_fee == 0.0


def test_inflation_increases_fee():
    out = calculate(_make_inputs(periods=24))
    # Year 2 fees should be higher than year 1
    assert out.imbalance_fee_monthly[12] > out.imbalance_fee_monthly[0]


def test_higher_imbalance_pct():
    low = calculate(_make_inputs(imbalance_pct=0.03))
    high = calculate(_make_inputs(imbalance_pct=0.10))
    assert high.total_imbalance_fee > low.total_imbalance_fee


def test_total_equals_sum():
    out = calculate(_make_inputs())
    assert out.total_imbalance_fee == pytest.approx(sum(out.imbalance_fee_monthly))


def test_output_lengths():
    out = calculate(_make_inputs(periods=60))
    assert len(out.imbalance_fee_monthly) == 60


def test_output_is_outputs():
    out = calculate(_make_inputs())
    assert isinstance(out, Outputs)


def test_validation_wrong_length():
    with pytest.raises(ValueError, match="production_MWh length"):
        Inputs(
            periods=10, start_year=2026, start_month=1,
            fee_per_MWh=5.0, production_MWh=[100.0] * 5,
        )


def test_higher_production_higher_fee():
    low = calculate(_make_inputs(production=500.0))
    high = calculate(_make_inputs(production=2000.0))
    assert high.total_imbalance_fee > low.total_imbalance_fee


def test_fee_never_negative():
    out = calculate(_make_inputs())
    assert all(f >= 0 for f in out.imbalance_fee_monthly)


def test_excel_formulas():
    from modules.costs.IMBALANCE_FEE_001 import get_excel_formulas
    refs = {"production": "C10", "imbalance_pct": "$B$5",
            "fee_per_MWh": "$B$6", "inflation_factor": "C20"}
    formulas = get_excel_formulas(refs)
    assert "imbalance_fee" in formulas
    assert formulas["imbalance_fee"].startswith("=")


def test_no_inflation_flat_fees():
    out = calculate(_make_inputs(inflation_rate=0.0, periods=24))
    assert out.imbalance_fee_monthly[0] == pytest.approx(out.imbalance_fee_monthly[23])


def test_100pct_imbalance():
    out = calculate(_make_inputs(imbalance_pct=1.0, production=100.0, fee=10.0))
    # 100 * 1.0 * 10 / 1000 = 1.0 DKKk
    assert out.imbalance_fee_monthly[0] == pytest.approx(1.0)
