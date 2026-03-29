"""
MODULE_ID:    DIV_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-29
MODIFIED:     2026-03-29

Dividend distribution + capital reduction waterfall.

Three-gate dividend logic (matches EE Master Model C_Financing):
  Gate 1: operations_flag — dividends only post-COD
  Gate 2: retained_earnings > 0 — cannot distribute more than earned
  Gate 3: cash_available > reserve — maintain minimum cash buffer

Formula:
  dividend = IF(operations, MAX(MIN(retained_earnings, cash - reserve), 0) * payout_ratio, 0)

Capital reduction: after dividends, return contributed equity from remaining
cash above a higher threshold. Applied at end of operations or periodically.

Source: EE Master Model v1.0, C_Financing rows 692-714
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Optional


class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)

    # Gate 1: operations timing
    cod_period: int = Field(0, ge=0, description="First operational period (0-based)")
    ops_end_period: Optional[int] = Field(
        None, description="Last operational period. None = end of model."
    )

    # Gate 2+3 inputs (from CF_001 pass without dividends)
    net_income: list[float] = Field(..., description="Monthly net income from PL_001")
    closing_cash: list[float] = Field(..., description="Monthly closing cash from CF_001 (pre-dividend)")

    # Opening balances
    opening_retained_earnings: float = Field(0.0, description="RE at model start")
    opening_contributed_equity: float = Field(0.0, description="Contributed equity at model start")

    # Distribution parameters
    payout_ratio: float = Field(0.80, ge=0, le=1.0, description="Fraction of distributable to pay as dividend")
    cash_reserve: float = Field(40.0, ge=0, description="Minimum cash reserve after distribution")
    payment_frequency: int = Field(
        6, ge=1, le=12, description="Months between payments (6 = semi-annual, 12 = annual)"
    )
    payment_months: list[int] = Field(
        default_factory=lambda: [6, 12],
        description="Calendar months when dividends are paid (e.g. [6, 12] = Jun/Dec)"
    )

    # Capital reduction
    capital_reduction_active: bool = Field(False, description="Enable capital reduction")
    capital_reduction_threshold: float = Field(
        500.0, ge=0, description="Cash above this level triggers capital reduction"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.net_income) != n:
            raise ValueError(f"net_income length {len(self.net_income)} != periods {n}")
        if len(self.closing_cash) != n:
            raise ValueError(f"closing_cash length {len(self.closing_cash)} != periods {n}")
        for m in self.payment_months:
            if m < 1 or m > 12:
                raise ValueError(f"payment_months must be 1-12, got {m}")
        return self


class Outputs(BaseModel):
    # Monthly arrays
    dividends_paid: list[float]         # positive = cash out
    capital_reduction: list[float]      # positive = cash out (equity returned)
    total_distribution: list[float]     # dividends + capital reduction
    retained_earnings: list[float]      # running RE balance (for gate 2)
    distributable_cash: list[float]     # cash above reserve (for gate 3)

    # Scalars
    total_dividends: float
    total_capital_reduction: float
    distribution_periods: int           # count of periods with non-zero distribution


def calculate(inputs: Inputs) -> Outputs:
    """Three-gate dividend waterfall with optional capital reduction."""
    n = inputs.periods
    ops_end = inputs.ops_end_period if inputs.ops_end_period is not None else n

    dividends = [0.0] * n
    cap_red = [0.0] * n
    total_dist = [0.0] * n
    re_series = [0.0] * n
    dist_cash = [0.0] * n

    re = inputs.opening_retained_earnings
    contributed_equity = inputs.opening_contributed_equity
    equity_remaining = contributed_equity
    pay_set = set(inputs.payment_months)

    for p in range(n):
        # Update retained earnings (cumulative net income)
        re += inputs.net_income[p]
        re_series[p] = re

        # Gate 1: must be in operations window
        cal_month = ((inputs.start_month - 1 + p) % 12) + 1
        if p < inputs.cod_period or p > ops_end or cal_month not in pay_set:
            continue

        # Gate 2: retained earnings must be positive
        if re <= 0:
            continue

        # Gate 3: cash above reserve
        available = inputs.closing_cash[p] - inputs.cash_reserve
        dist_cash[p] = max(0, available)
        if available <= 0:
            continue

        # Dividend = min(RE, available) × payout_ratio
        distributable = min(re, available)
        div = distributable * inputs.payout_ratio
        if div > 0:
            dividends[p] = div
            re -= div  # dividends reduce RE

        # Capital reduction: return equity from remaining cash
        if inputs.capital_reduction_active and equity_remaining > 0:
            remaining_cash = available - div
            if remaining_cash > inputs.capital_reduction_threshold:
                cap_return = min(
                    remaining_cash - inputs.capital_reduction_threshold,
                    equity_remaining,
                )
                if cap_return > 0:
                    cap_red[p] = cap_return
                    equity_remaining -= cap_return

        total_dist[p] = dividends[p] + cap_red[p]

    dist_periods = sum(1 for v in total_dist if v > 0)

    return Outputs(
        dividends_paid=dividends,
        capital_reduction=cap_red,
        total_distribution=total_dist,
        retained_earnings=re_series,
        distributable_cash=dist_cash,
        total_dividends=sum(dividends),
        total_capital_reduction=sum(cap_red),
        distribution_periods=dist_periods,
    )


def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "dividends_paid": (
            f"=IF(AND({r['ops_flag']}=1,{r['retained_earnings']}>0),"
            f"MAX(MIN({r['retained_earnings']},{r['cash']}-{r['reserve']}),0)"
            f"*{r['payout_ratio']},0)"
        ),
        "retained_earnings": f"={r['prev_re']}+{r['net_income']}-{r['dividends']}",
        "total_distribution": f"={r['dividends']}+{r['cap_reduction']}",
    }
