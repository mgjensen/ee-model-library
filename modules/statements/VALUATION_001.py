"""
MODULE_ID:    VALUATION_001
VERSION:      1.1
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-18
MODIFIED:     2026-03-29

EV Bridge / Purchase Price Calculation.

Two modes:
  Buy-and-Hold: NPV of all FCF from closing to end of life.
  Buy-and-Sell: NPV of FCF from closing to sell-down, plus terminal value
    at sell-down (PV of remaining FCF from sell-down onward, optionally
    discounted at a compressed exit rate).

Also computes incoming investor IRR: purchase → cash flows → exit proceeds.
"""

from __future__ import annotations

import math
from pydantic import BaseModel, Field, model_validator
from typing import Optional


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

    # Buy-and-Sell (optional)
    sell_down_period: Optional[int] = Field(
        None, ge=0, description="Period of sell-down. None = Buy-and-Hold only."
    )
    exit_discount_rate: Optional[float] = Field(
        None, description="Discount rate for terminal value at sell-down. None = same as discount_rate."
    )
    debt_at_sell_down: float = Field(0.0, description="Debt outstanding at sell-down DKKk")
    cash_at_sell_down: float = Field(0.0, description="Cash at sell-down DKKk")

    # Equity cash flow for incoming investor IRR (optional)
    equity_cash_flow: Optional[list[float]] = Field(
        None, description="Monthly equity cash flow DKKk for incoming investor IRR"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.fcf_monthly) != n:
            raise ValueError(
                f"fcf_monthly length {len(self.fcf_monthly)} != periods {n}"
            )
        if self.equity_cash_flow is not None and len(self.equity_cash_flow) != n:
            raise ValueError(
                f"equity_cash_flow length {len(self.equity_cash_flow)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    # Buy-and-Hold
    ev: float                          # NPV of FCF from closing onward (DKKk)
    ev_per_mw: float                   # EV / capacity_mw (DKKk/MW)
    ev_per_mwh: float                  # EV / capacity_mwh (0 if no BESS)
    purchase_price_100pct: float       # EV − debt + cash − NWC − other (DKKk)
    purchase_price_investor: float     # purchase_price_100pct × investor_pct
    total_equity_ticket_100pct: float  # purchase_price + remaining capex
    total_equity_ticket_investor: float  # investor share of equity ticket
    gearing_at_closing: float          # debt / (debt + purchase_price) or 0
    remaining_capex_after_closing: float  # pass-through

    # Buy-and-Sell
    sell_down_ev: float                # Terminal value at sell-down (DKKk)
    buy_and_sell_ev: float             # PV of FCF to sell-down + terminal value (DKKk)
    buy_and_sell_equity: float         # Buy-and-Sell equity value at closing

    # Incoming investor IRR
    incoming_investor_irr: float       # Annualised IRR on equity cash flows


# ============================================================================
# CORE CALCULATION
# ============================================================================

def _npv(cash_flows: list[float], monthly_rate: float) -> float:
    total = 0.0
    discount = 1.0
    for cf in cash_flows:
        total += cf / discount
        discount *= (1.0 + monthly_rate)
        if discount == 0.0:
            break
    return total


def _irr_monthly(cash_flows: list[float], tol: float = 1e-8, max_iter: int = 200) -> float:
    has_pos = any(cf > 0 for cf in cash_flows)
    has_neg = any(cf < 0 for cf in cash_flows)
    if not (has_pos and has_neg):
        return float("nan")
    for lo, hi in [(-0.001, 0.1), (-0.01, 0.5), (-0.05, 1.0)]:
        npv_lo = _npv(cash_flows, lo)
        npv_hi = _npv(cash_flows, hi)
        if npv_lo * npv_hi <= 0:
            break
    else:
        return float("nan")
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        npv_mid = _npv(cash_flows, mid)
        if abs(npv_mid) < tol or (hi - lo) / 2.0 < tol:
            return mid
        if npv_lo * npv_mid < 0:
            hi = mid
        else:
            lo = mid
            npv_lo = npv_mid
    return (lo + hi) / 2.0


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

    # Buy-and-Sell: terminal value at sell-down period
    sdp = inputs.sell_down_period
    if sdp is not None and sdp < n:
        exit_rate = inputs.exit_discount_rate if inputs.exit_discount_rate is not None else dr
        exit_monthly = (1.0 + exit_rate) ** (1.0 / 12.0) - 1.0
        # Terminal value = PV of FCF from sell-down onward (discounted at exit rate)
        tv = 0.0
        for t in range(sdp, n):
            tv += fcf[t] / (1.0 + exit_monthly) ** (t - sdp)
        # Buy-and-Sell EV = PV of FCF from closing to sell-down + PV of terminal value
        bas_ev = 0.0
        for t in range(cp, sdp):
            bas_ev += fcf[t] / (1.0 + monthly_rate) ** (t - cp)
        # Add discounted terminal value
        bas_ev += tv / (1.0 + monthly_rate) ** (sdp - cp)
        bas_equity = bas_ev - debt + cash - nwc - other
    else:
        tv = 0.0
        bas_ev = ev  # same as buy-and-hold
        bas_equity = purchase_100

    # Incoming investor IRR: bisection on equity cash flows
    inv_irr = float("nan")
    if inputs.equity_cash_flow is not None:
        ecf = inputs.equity_cash_flow
        inv_irr = _irr_monthly(ecf)
        if not math.isnan(inv_irr):
            inv_irr = (1.0 + inv_irr) ** 12 - 1.0  # annualise

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
        sell_down_ev=tv,
        buy_and_sell_ev=bas_ev,
        buy_and_sell_equity=bas_equity,
        incoming_investor_irr=inv_irr,
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
