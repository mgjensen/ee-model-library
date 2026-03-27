"""
MODULE_ID:    VALUATION_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-18
MODIFIED:     2026-03-18

EV Bridge / Purchase Price Calculation.

Computes enterprise value as NPV of free cash flow from closing onward,
then bridges to equity purchase price via debt, cash, NWC adjustments.
Splits 100% price into investor share and computes total equity ticket
including remaining capex after closing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    closing_period: int = Field(..., ge=0)

    # Valuation parameters
    discount_rate: float = Field(0.085, description="Annual discount rate for EV NPV")
    capacity_mw: float = Field(0.0, gt=0, description="Installed capacity MW")
    capacity_mwh: float = Field(0.0, ge=0, description="Storage capacity MWh (0 if no BESS)")

    # Cash flow series (DKKk, monthly)
    fcf_monthly: list[float] = Field(
        ..., description="Monthly free cash flow DKKk"
    )

    # EV bridge adjustments at closing (DKKk)
    debt_outstanding_at_closing: float = Field(0.0, description="Senior debt outstanding at closing DKKk")
    cash_at_closing: float = Field(0.0, description="Cash on balance sheet at closing DKKk")
    nwc_at_closing: float = Field(0.0, description="Net working capital at closing DKKk")
    other_adjustments: float = Field(0.0, description="Other EV-to-equity adjustments DKKk")

    # Post-closing capex
    remaining_capex_after_closing: float = Field(0.0, ge=0, description="Remaining capex after closing DKKk")

    # Investor share
    investor_pct: float = Field(0.50, ge=0, le=1.0, description="Investor ownership percentage")

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.fcf_monthly) != n:
            raise ValueError(
                f"fcf_monthly length {len(self.fcf_monthly)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    ev: float                          # NPV of FCF from closing onward (DKKk)
    ev_per_mw: float                   # EV / capacity_mw (DKKk/MW)
    ev_per_mwh: float                  # EV / capacity_mwh (0 if no BESS)
    purchase_price_100pct: float       # EV − debt + cash − NWC − other (DKKk)
    purchase_price_investor: float     # purchase_price_100pct × investor_pct
    total_equity_ticket_100pct: float  # purchase_price + remaining capex
    total_equity_ticket_investor: float  # investor share of equity ticket
    gearing_at_closing: float          # debt / (debt + purchase_price) or 0
    remaining_capex_after_closing: float  # pass-through


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Compute EV bridge and purchase price allocation."""
    n = inputs.periods
    cp = inputs.closing_period
    fcf = inputs.fcf_monthly
    dr = inputs.discount_rate
    debt = inputs.debt_outstanding_at_closing
    cash = inputs.cash_at_closing
    nwc = inputs.nwc_at_closing
    other = inputs.other_adjustments
    rcapex = inputs.remaining_capex_after_closing
    inv_pct = inputs.investor_pct

    # EV = NPV of FCF from closing_period onward
    monthly_rate = (1.0 + dr) ** (1.0 / 12.0) - 1.0
    ev = 0.0
    for t in range(cp, n):
        periods_from_closing = t - cp
        ev += fcf[t] / (1.0 + monthly_rate) ** periods_from_closing

    # EV multiples
    ev_per_mw = ev / inputs.capacity_mw if inputs.capacity_mw > 0 else 0.0
    ev_per_mwh = ev / inputs.capacity_mwh if inputs.capacity_mwh > 0 else 0.0

    # Purchase price bridge
    purchase_100 = ev - debt + cash - nwc - other
    purchase_inv = purchase_100 * inv_pct

    # Total equity ticket (purchase + remaining capex)
    ticket_100 = purchase_100 + rcapex
    ticket_inv = purchase_inv + rcapex * inv_pct

    # Gearing
    denom = debt + purchase_100
    gearing = debt / denom if denom > 0 else 0.0

    return Outputs(
        ev=ev,
        ev_per_mw=ev_per_mw,
        ev_per_mwh=ev_per_mwh,
        purchase_price_100pct=purchase_100,
        purchase_price_investor=purchase_inv,
        total_equity_ticket_100pct=ticket_100,
        total_equity_ticket_investor=ticket_inv,
        gearing_at_closing=gearing,
        remaining_capex_after_closing=rcapex,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    refs keys: fcf_range, discount_rate, debt, cash, nwc, other,
               remaining_capex, investor_pct, capacity_mw, capacity_mwh
    """
    r = refs
    return {
        "ev": f"=NPV((1+{r['discount_rate']})^(1/12)-1,{r['fcf_range']})",
        "purchase_price_100pct": f"={r['ev']}-{r['debt']}+{r['cash']}-{r['nwc']}-{r['other']}",
        "purchase_price_investor": f"={r['purchase_100']}*{r['investor_pct']}",
        "total_equity_ticket_100pct": f"={r['purchase_100']}+{r['remaining_capex']}",
        "gearing_at_closing": f"=IF({r['debt']}+{r['purchase_100']}>0,{r['debt']}/({r['debt']}+{r['purchase_100']}),0)",
    }
