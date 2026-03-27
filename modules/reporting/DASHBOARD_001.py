"""
MODULE_ID:    DASHBOARD_001
VERSION:      1.0
TIER:         both
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

KPI dashboard and Debt Cockpit data extraction.

Computes all headline metrics for a project: enterprise value, purchase price
bridge, equity ticket, profit metrics, and passes through coverage ratios and
IRRs from upstream modules.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # IRR / NPV
    project_irr: float = Field(0.0)
    equity_irr: float = Field(0.0)
    project_npv: float = Field(0.0)
    equity_npv: float = Field(0.0)
    # Capacity
    res_capacity_MW: float = Field(0.0, ge=0)
    bess_capacity_MW: float = Field(0.0, ge=0)
    bess_capacity_MWh: float = Field(0.0, ge=0)
    production_year1_MWh: float = Field(0.0, ge=0)
    # Financials
    total_capex_DKKk: float = Field(0.0, ge=0)
    remaining_capex_after_closing_DKKk: float = Field(0.0, ge=0)
    total_debt_DKKk: float = Field(0.0, ge=0)
    gearing_pct: float = Field(0.0)
    sizing_constraint: str = Field("leverage_cap", description="'leverage_cap' or 'dscr'")
    # DSCR / LLCR
    avg_dscr: float = Field(0.0)
    min_dscr: float = Field(0.0)
    avg_llcr: float = Field(0.0)
    min_llcr: float = Field(0.0)
    # Cash
    cash_at_closing_DKKk: float = Field(0.0)
    nwc_at_closing_DKKk: float = Field(0.0)
    # Lifetime totals
    total_revenue_lifetime: float = Field(0.0)
    total_opex_lifetime: float = Field(0.0)
    total_ebitda_lifetime: float = Field(0.0)
    total_tax_lifetime: float = Field(0.0)
    # Investor
    investor_ownership_pct: float = Field(0.50, ge=0, le=1.0)


class Outputs(BaseModel):
    project_irr: float
    equity_irr: float
    ev_DKKk: float
    ev_per_MW: float
    ev_per_MWh: float
    total_consideration_DKKk: float
    purchase_price_100pct: float
    purchase_price_investor_pct: float
    total_equity_ticket_investor: float
    total_debt_supported: float
    gearing_actual: float
    sizing_constraint: str
    avg_dscr: float
    min_dscr: float
    avg_llcr: float
    min_llcr: float
    profit_from_sale_DKKk: float
    profit_over_capex_pct: float
    capex_per_MW: float
    ebitda_margin: float
    total_revenue_lifetime: float
    total_opex_lifetime: float
    total_ebitda_lifetime: float


# ============================================================================
# CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Compute headline KPIs from upstream module results."""
    inp = inputs

    total_capacity = inp.res_capacity_MW + inp.bess_capacity_MW

    # Enterprise value = NPV of project cash flows
    ev = inp.project_npv

    ev_per_MW = ev / total_capacity if total_capacity > 0 else 0.0
    ev_per_MWh = ev / inp.production_year1_MWh if inp.production_year1_MWh > 0 else 0.0

    # Purchase price bridge
    purchase_price_100 = ev - inp.total_debt_DKKk - inp.cash_at_closing_DKKk - inp.nwc_at_closing_DKKk
    purchase_price_investor = purchase_price_100 * inp.investor_ownership_pct

    # Equity ticket = purchase price share + remaining capex share
    equity_ticket = purchase_price_investor + inp.remaining_capex_after_closing_DKKk * inp.investor_ownership_pct

    # Total consideration = EV (debt + equity value of the project)
    total_consideration = ev

    # Profit metrics
    profit = ev - inp.total_capex_DKKk
    profit_over_capex = profit / inp.total_capex_DKKk if inp.total_capex_DKKk > 0 else 0.0

    capex_per_MW = inp.total_capex_DKKk / total_capacity if total_capacity > 0 else 0.0

    ebitda_margin = inp.total_ebitda_lifetime / inp.total_revenue_lifetime if inp.total_revenue_lifetime > 0 else 0.0

    return Outputs(
        project_irr=inp.project_irr,
        equity_irr=inp.equity_irr,
        ev_DKKk=ev,
        ev_per_MW=ev_per_MW,
        ev_per_MWh=ev_per_MWh,
        total_consideration_DKKk=total_consideration,
        purchase_price_100pct=purchase_price_100,
        purchase_price_investor_pct=purchase_price_investor,
        total_equity_ticket_investor=equity_ticket,
        total_debt_supported=inp.total_debt_DKKk,
        gearing_actual=inp.gearing_pct,
        sizing_constraint=inp.sizing_constraint,
        avg_dscr=inp.avg_dscr,
        min_dscr=inp.min_dscr,
        avg_llcr=inp.avg_llcr,
        min_llcr=inp.min_llcr,
        profit_from_sale_DKKk=profit,
        profit_over_capex_pct=profit_over_capex,
        capex_per_MW=capex_per_MW,
        ebitda_margin=ebitda_margin,
        total_revenue_lifetime=inp.total_revenue_lifetime,
        total_opex_lifetime=inp.total_opex_lifetime,
        total_ebitda_lifetime=inp.total_ebitda_lifetime,
    )


# ============================================================================
# EXCEL FORMULAS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows."""
    return {
        "ev_DKKk": "=project_npv",
        "ev_per_MW": "=ev_DKKk / (res_capacity_MW + bess_capacity_MW)",
        "ev_per_MWh": "=ev_DKKk / production_year1_MWh",
        "purchase_price_100pct": "=ev_DKKk - total_debt_DKKk - cash_at_closing - nwc_at_closing",
        "purchase_price_investor_pct": "=purchase_price_100pct * investor_ownership_pct",
        "total_equity_ticket_investor": "=purchase_price_investor_pct + remaining_capex * investor_ownership_pct",
        "profit_from_sale_DKKk": "=ev_DKKk - total_capex_DKKk",
        "profit_over_capex_pct": "=profit_from_sale_DKKk / total_capex_DKKk",
        "capex_per_MW": "=total_capex_DKKk / (res_capacity_MW + bess_capacity_MW)",
        "ebitda_margin": "=total_ebitda_lifetime / total_revenue_lifetime",
    }
