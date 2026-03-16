"""
MODULE_ID:    WACC_001
VERSION:      1.0
TIER:         simplified / detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Two-layer WACC calculation.
PPA layer: contracted revenue, reduced market risk.
Merchant layer: market-exposed revenue, full market risk.
Blended CoE weighted by revenue share.

Source: model_complete.py v0.6 — European Energy A/S
"""

import json
import numpy as np
from pathlib import Path
from pydantic import BaseModel, Field

# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    country: str = Field(..., description="Market: DK, DE, AU")
    ppa_share: float = Field(..., ge=0.0, le=1.0, description="Share of revenue under PPA")
    debt_aud: float = Field(..., gt=0, description="Total debt amount")
    equity_aud: float = Field(..., gt=0, description="Total equity amount")
    merchant_erp_adjustment: float = Field(1.0, description="Risk multiplier for merchant portion")


class Outputs(BaseModel):
    wacc: float
    blended_cost_of_equity: float
    ppa_cost_of_equity: float
    merchant_cost_of_equity: float
    cost_of_debt_posttax: float
    levered_beta: float
    debt_to_equity_ratio: float
    ppa_erp: float
    merchant_erp: float


# ============================================================================
# ASSUMPTION DB LOADER
# ============================================================================

def get_default_assumptions(market: str) -> dict:
    """Load cost of capital assumptions from assumption_db."""
    db_path = Path(__file__).parents[2] / "registry" / "assumption_db" / f"{market}.json"
    if not db_path.exists():
        raise FileNotFoundError(f"No assumption file for market: {market}")
    with open(db_path) as f:
        data = json.load(f)
    return data["cost_of_capital"]


# ============================================================================
# CORE CALCULATIONS
# ============================================================================

def calculate_levered_beta(
    unlevered_beta: float,
    debt_to_equity: float,
    tax_rate: float
) -> float:
    """Hamada equation: levered beta from asset beta."""
    return unlevered_beta * (1 + (1 - tax_rate) * debt_to_equity)


def calculate_two_layer_coe(
    risk_free_rate: float,
    levered_beta: float,
    erp: float,
    ppa_share: float,
    ppa_erp_reduction_factor: float,
    merchant_erp_multiplier: float,
) -> dict:
    """
    Two-layer cost of equity.
    PPA layer:      CoE = Rf + Beta × (ERP × (1 - reduction))
    Merchant layer: CoE = Rf + Beta × (ERP × multiplier)
    Blended:        weighted by PPA share
    """
    merchant_share = 1 - ppa_share

    ppa_erp = erp * (1 - ppa_erp_reduction_factor)
    ppa_coe = risk_free_rate + levered_beta * ppa_erp

    merchant_erp = erp * merchant_erp_multiplier
    merchant_coe = risk_free_rate + levered_beta * merchant_erp

    blended_coe = ppa_share * ppa_coe + merchant_share * merchant_coe

    return {
        "ppa_erp": ppa_erp,
        "ppa_cost_of_equity": ppa_coe,
        "merchant_erp": merchant_erp,
        "merchant_cost_of_equity": merchant_coe,
        "blended_cost_of_equity": blended_coe,
    }


def calculate(inputs: Inputs) -> Outputs:
    """Pure Python WACC calculation. Used for testing and API layer."""
    coc = get_default_assumptions(inputs.country)

    debt_to_equity = inputs.debt_aud / inputs.equity_aud
    levered_beta = calculate_levered_beta(
        coc["unlevered_beta"], debt_to_equity, coc["tax_rate"]
    )

    coe = calculate_two_layer_coe(
        risk_free_rate=coc["risk_free_rate"],
        levered_beta=levered_beta,
        erp=coc["erp"],
        ppa_share=inputs.ppa_share,
        ppa_erp_reduction_factor=coc["ppa_erp_reduction_factor"],
        merchant_erp_multiplier=coc["merchant_erp_multiplier"] * inputs.merchant_erp_adjustment,
    )

    total_capital = inputs.debt_aud + inputs.equity_aud
    debt_weight = inputs.debt_aud / total_capital
    equity_weight = inputs.equity_aud / total_capital

    wacc = equity_weight * coe["blended_cost_of_equity"] + debt_weight * coc["cost_of_debt_posttax"]

    return Outputs(
        wacc=wacc,
        blended_cost_of_equity=coe["blended_cost_of_equity"],
        ppa_cost_of_equity=coe["ppa_cost_of_equity"],
        merchant_cost_of_equity=coe["merchant_cost_of_equity"],
        cost_of_debt_posttax=coc["cost_of_debt_posttax"],
        levered_beta=levered_beta,
        debt_to_equity_ratio=debt_to_equity,
        ppa_erp=coe["ppa_erp"],
        merchant_erp=coe["merchant_erp"],
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings.
    refs keys: unlevered_beta, tax_rate, debt, equity, rf, erp,
               ppa_share, ppa_reduction, merchant_mult, cost_of_debt
    """
    r = refs
    return {
        "levered_beta": f"={r['unlevered_beta']}*(1+(1-{r['tax_rate']})*({r['debt']}/{r['equity']}))",
        "ppa_erp":      f"={r['erp']}*(1-{r['ppa_reduction']})",
        "merchant_erp": f"={r['erp']}*{r['merchant_mult']}",
        "ppa_coe":      f"={r['rf']}+{r['levered_beta']}*{r['ppa_erp']}",
        "merchant_coe": f"={r['rf']}+{r['levered_beta']}*{r['merchant_erp']}",
        "blended_coe":  f"={r['ppa_share']}*{r['ppa_coe']}+(1-{r['ppa_share']})*{r['merchant_coe']}",
        "wacc":         f"=({r['equity']}/({r['debt']}+{r['equity']}))*{r['blended_coe']}+({r['debt']}/({r['debt']}+{r['equity']}))*{r['cost_of_