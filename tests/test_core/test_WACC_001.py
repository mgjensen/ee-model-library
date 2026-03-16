"""Tests for WACC_001 — two-layer cost of capital module."""

import pytest
from modules.core.WACC_001 import (
    Inputs,
    Outputs,
    calculate,
    calculate_levered_beta,
    calculate_two_layer_coe,
    get_excel_formulas,
)


# ---------------------------------------------------------------------------
# Unit: calculate_levered_beta (Hamada equation)
# ---------------------------------------------------------------------------

def test_levered_beta_zero_leverage():
    """At D/E=0, levered beta == unlevered beta."""
    assert calculate_levered_beta(0.46, 0.0, 0.22) == pytest.approx(0.46)


def test_levered_beta_known_value():
    """DK capital structure: D/E = 0.80/0.20 = 4.0"""
    # 0.46 * (1 + (1 - 0.22) * 4.0) = 0.46 * (1 + 3.12) = 0.46 * 4.12 = 1.8952
    result = calculate_levered_beta(0.46, 4.0, 0.22)
    assert result == pytest.approx(1.8952, rel=1e-4)


# ---------------------------------------------------------------------------
# Unit: calculate_two_layer_coe
# ---------------------------------------------------------------------------

def test_two_layer_coe_full_ppa():
    """At 100% PPA share, blended CoE == PPA CoE."""
    result = calculate_two_layer_coe(
        risk_free_rate=0.026,
        levered_beta=1.8952,
        erp=0.053,
        ppa_share=1.0,
        ppa_erp_reduction_factor=0.90,
        merchant_erp_multiplier=1.0,
    )
    assert result["blended_cost_of_equity"] == pytest.approx(result["ppa_cost_of_equity"])


def test_two_layer_coe_full_merchant():
    """At 0% PPA share, blended CoE == merchant CoE."""
    result = calculate_two_layer_coe(
        risk_free_rate=0.026,
        levered_beta=1.8952,
        erp=0.053,
        ppa_share=0.0,
        ppa_erp_reduction_factor=0.90,
        merchant_erp_multiplier=1.0,
    )
    assert result["blended_cost_of_equity"] == pytest.approx(result["merchant_cost_of_equity"])


def test_two_layer_coe_ppa_below_merchant():
    """PPA CoE < merchant CoE when ppa_erp_reduction_factor > 0."""
    result = calculate_two_layer_coe(
        risk_free_rate=0.026,
        levered_beta=1.8952,
        erp=0.053,
        ppa_share=0.7,
        ppa_erp_reduction_factor=0.90,
        merchant_erp_multiplier=1.0,
    )
    assert result["ppa_cost_of_equity"] < result["merchant_cost_of_equity"]


def test_two_layer_coe_blended_between_layers():
    """Blended CoE must sit between PPA and merchant CoE."""
    result = calculate_two_layer_coe(
        risk_free_rate=0.026,
        levered_beta=1.8952,
        erp=0.053,
        ppa_share=0.7,
        ppa_erp_reduction_factor=0.90,
        merchant_erp_multiplier=1.0,
    )
    assert result["ppa_cost_of_equity"] <= result["blended_cost_of_equity"] <= result["merchant_cost_of_equity"]


# ---------------------------------------------------------------------------
# Integration: calculate() — uses DK assumption_db
# ---------------------------------------------------------------------------

DK_BASE = Inputs(
    country="DK",
    ppa_share=0.7,
    debt_aud=80_000_000,
    equity_aud=20_000_000,
)


def test_calculate_returns_outputs_instance():
    assert isinstance(calculate(DK_BASE), Outputs)


def test_calculate_wacc_positive():
    assert calculate(DK_BASE).wacc > 0


def test_calculate_wacc_reasonable_range():
    """WACC should be between 2% and 20% for typical inputs."""
    result = calculate(DK_BASE)
    assert 0.02 < result.wacc < 0.20


def test_calculate_debt_to_equity_ratio():
    result = calculate(DK_BASE)
    assert result.debt_to_equity_ratio == pytest.approx(4.0)


def test_calculate_wacc_dk_numeric():
    """
    Spot-check against manually computed value.
    D/E = 4.0, levered_beta = 0.46*(1+0.78*4) = 1.8952
    ppa_erp  = 0.053*(1-0.90) = 0.0053
    merch_erp = 0.053*1.0     = 0.053
    ppa_coe  = 0.026 + 1.8952*0.0053 = 0.036045
    merch_coe = 0.026 + 1.8952*0.053 = 0.126446
    blended  = 0.7*0.036045 + 0.3*0.126446 = 0.063367
    equity_w = 20/100 = 0.20, debt_w = 0.80
    wacc = 0.20*0.063367 + 0.80*0.035 = 0.040673
    """
    result = calculate(DK_BASE)
    assert result.wacc == pytest.approx(0.040673, rel=1e-3)
    assert result.levered_beta == pytest.approx(1.8952, rel=1e-3)


def test_calculate_higher_merchant_share_raises_wacc():
    """More merchant exposure should yield higher WACC."""
    low_merchant = calculate(Inputs(country="DK", ppa_share=0.9,
                                    debt_aud=80_000_000, equity_aud=20_000_000))
    high_merchant = calculate(Inputs(country="DK", ppa_share=0.1,
                                     debt_aud=80_000_000, equity_aud=20_000_000))
    assert low_merchant.wacc < high_merchant.wacc


def test_calculate_merchant_erp_adjustment():
    """merchant_erp_adjustment > 1 should raise WACC."""
    base = calculate(DK_BASE)
    stressed = calculate(Inputs(country="DK", ppa_share=0.7,
                                debt_aud=80_000_000, equity_aud=20_000_000,
                                merchant_erp_adjustment=1.5))
    assert stressed.wacc > base.wacc


def test_calculate_invalid_country():
    with pytest.raises(FileNotFoundError):
        calculate(Inputs(country="XX", ppa_share=0.5,
                         debt_aud=1_000_000, equity_aud=500_000))


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_returns_all_keys():
    refs = {
        "unlevered_beta": "B1", "tax_rate": "B2", "debt": "B3", "equity": "B4",
        "rf": "B5", "erp": "B6", "ppa_share": "B7", "ppa_reduction": "B8",
        "merchant_mult": "B9", "cost_of_debt": "B10",
        "levered_beta": "C1", "ppa_erp": "C2", "merchant_erp": "C3",
        "ppa_coe": "C4", "merchant_coe": "C5", "blended_coe": "C6",
    }
    formulas = get_excel_formulas(refs)
    for key in ("levered_beta", "ppa_erp", "merchant_erp",
                "ppa_coe", "merchant_coe", "blended_coe", "wacc"):
        assert key in formulas
        assert formulas[key].startswith("=")


def test_get_excel_formulas_cell_refs_appear():
    refs = {
        "unlevered_beta": "B1", "tax_rate": "B2", "debt": "B3", "equity": "B4",
        "rf": "B5", "erp": "B6", "ppa_share": "B7", "ppa_reduction": "B8",
        "merchant_mult": "B9", "cost_of_debt": "B10",
        "levered_beta": "C1", "ppa_erp": "C2", "merchant_erp": "C3",
        "ppa_coe": "C4", "merchant_coe": "C5", "blended_coe": "C6",
    }
    formulas = get_excel_formulas(refs)
    assert "B3" in formulas["levered_beta"]
    assert "B10" in formulas["wacc"]
