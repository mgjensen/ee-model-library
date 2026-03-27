"""
MODULE_ID:    SOURCES_USES_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Construction-period sources & uses waterfall.
Determines equity requirement as residual after all debt sources.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    construction_start_period: int = Field(0, ge=0)
    cod_period: int = Field(..., gt=0)

    # Uses
    total_capex_DKKk: float = Field(0.0, ge=0)
    vat_on_capex_DKKk: float = Field(0.0, ge=0)
    arrangement_fees_total_DKKk: float = Field(0.0, ge=0)
    idc_construction_DKKk: float = Field(0.0, ge=0)
    idc_senior_DKKk: float = Field(0.0, ge=0)
    commitment_fees_DKKk: float = Field(0.0, ge=0)
    vat_facility_costs_DKKk: float = Field(0.0, ge=0)
    dsra_initial_DKKk: float = Field(0.0, ge=0)
    minimum_cash_DKKk: float = Field(0.0, ge=0)
    pre_completion_opex_net_DKKk: float = Field(0.0, ge=0)

    # Sources (debt)
    senior_debt_drawn_DKKk: float = Field(0.0, ge=0)
    shl_drawn_DKKk: float = Field(0.0, ge=0)
    vat_facility_drawn_DKKk: float = Field(0.0, ge=0)
    pre_completion_revenue_DKKk: float = Field(0.0, ge=0)
    vat_reimbursements_DKKk: float = Field(0.0, ge=0)

    @model_validator(mode="after")
    def _validate(self):
        if self.cod_period > self.periods:
            raise ValueError(
                f"cod_period {self.cod_period} > periods {self.periods}"
            )
        if self.construction_start_period >= self.cod_period:
            raise ValueError(
                f"construction_start_period {self.construction_start_period} "
                f">= cod_period {self.cod_period}"
            )
        return self


class Outputs(BaseModel):
    # Uses
    uses_capex: float
    uses_vat: float
    uses_arrangement_fees: float
    uses_idc_construction: float
    uses_idc_senior: float
    uses_commitment_fees: float
    uses_vat_facility_costs: float
    uses_dsra: float
    uses_minimum_cash: float
    uses_pre_completion_opex: float
    total_uses: float

    # Sources
    sources_senior_debt: float
    sources_shl: float
    sources_vat_facility: float
    sources_pre_completion_revenue: float
    sources_vat_reimbursements: float
    sources_equity: float
    total_sources: float

    # Summary
    equity_required_DKKk: float
    gearing_pct: float
    equity_pct: float
    balance_check: float
    balance_ok: bool

    # Monthly equity disbursement (length = periods)
    equity_monthly: list[float]


# ============================================================================
# CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Core computation — pure Python, no side effects."""
    inp = inputs

    # Uses
    uses_capex = inp.total_capex_DKKk
    uses_vat = inp.vat_on_capex_DKKk
    uses_arrangement_fees = inp.arrangement_fees_total_DKKk
    uses_idc_construction = inp.idc_construction_DKKk
    uses_idc_senior = inp.idc_senior_DKKk
    uses_commitment_fees = inp.commitment_fees_DKKk
    uses_vat_facility_costs = inp.vat_facility_costs_DKKk
    uses_dsra = inp.dsra_initial_DKKk
    uses_minimum_cash = inp.minimum_cash_DKKk
    uses_pre_completion_opex = inp.pre_completion_opex_net_DKKk

    total_uses = (
        uses_capex
        + uses_vat
        + uses_arrangement_fees
        + uses_idc_construction
        + uses_idc_senior
        + uses_commitment_fees
        + uses_vat_facility_costs
        + uses_dsra
        + uses_minimum_cash
        + uses_pre_completion_opex
    )

    # Sources (non-equity)
    sources_senior_debt = inp.senior_debt_drawn_DKKk
    sources_shl = inp.shl_drawn_DKKk
    sources_vat_facility = inp.vat_facility_drawn_DKKk
    sources_pre_completion_revenue = inp.pre_completion_revenue_DKKk
    sources_vat_reimbursements = inp.vat_reimbursements_DKKk

    non_equity_sources = (
        sources_senior_debt
        + sources_shl
        + sources_vat_facility
        + sources_pre_completion_revenue
        + sources_vat_reimbursements
    )

    equity_required = max(0.0, total_uses - non_equity_sources)
    total_sources = non_equity_sources + equity_required

    balance_check = total_sources - total_uses
    balance_ok = abs(balance_check) < 1e-6

    gearing_pct = sources_senior_debt / total_uses if total_uses > 0 else 0.0
    equity_pct = equity_required / total_uses if total_uses > 0 else 0.0

    # Spread equity evenly across construction months
    construction_months = inp.cod_period - inp.construction_start_period
    equity_monthly = [0.0] * inp.periods
    if construction_months > 0 and equity_required > 0:
        monthly_amount = equity_required / construction_months
        for p in range(inp.construction_start_period, inp.cod_period):
            equity_monthly[p] = monthly_amount

    return Outputs(
        uses_capex=uses_capex,
        uses_vat=uses_vat,
        uses_arrangement_fees=uses_arrangement_fees,
        uses_idc_construction=uses_idc_construction,
        uses_idc_senior=uses_idc_senior,
        uses_commitment_fees=uses_commitment_fees,
        uses_vat_facility_costs=uses_vat_facility_costs,
        uses_dsra=uses_dsra,
        uses_minimum_cash=uses_minimum_cash,
        uses_pre_completion_opex=uses_pre_completion_opex,
        total_uses=total_uses,
        sources_senior_debt=sources_senior_debt,
        sources_shl=sources_shl,
        sources_vat_facility=sources_vat_facility,
        sources_pre_completion_revenue=sources_pre_completion_revenue,
        sources_vat_reimbursements=sources_vat_reimbursements,
        sources_equity=equity_required,
        total_sources=total_sources,
        equity_required_DKKk=equity_required,
        gearing_pct=gearing_pct,
        equity_pct=equity_pct,
        balance_check=balance_check,
        balance_ok=balance_ok,
        equity_monthly=equity_monthly,
    )


# ============================================================================
# EXCEL FORMULAS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows."""
    return {
        "total_uses": "=SUM(uses_capex:uses_pre_completion_opex)",
        "total_sources": "=SUM(sources_senior_debt:sources_equity)",
        "equity_required_DKKk": "=total_uses-SUM(sources_senior_debt:sources_vat_reimbursements)",
        "balance_check": "=total_sources-total_uses",
        "gearing_pct": "=sources_senior_debt/total_uses",
        "equity_pct": "=sources_equity/total_uses",
    }
