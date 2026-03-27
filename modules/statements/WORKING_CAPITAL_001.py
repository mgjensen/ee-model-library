"""
MODULE_ID:    WORKING_CAPITAL_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Working capital: receivables and payables by revenue stream with day-count delay.

Each revenue stream carries its own receivable-days assumption. Revenue is
recognised on accrual but cash is received after a lag of
max(1, round(receivable_days / 30)) months.  OPEX payables follow the same
logic with a single payable_days parameter.

Outputs:
    total_receivables     closing trade receivables (sum across streams)
    total_payables        closing trade payables (OPEX)
    working_capital_balance  receivables − payables
    working_capital_change   Δ balance period-on-period (feeds CF_001)
    total_revenue_received   cash collected from customers
    opex_paid                cash paid to suppliers
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# SUB-MODEL
# ============================================================================

class RevenueStream(BaseModel):
    name: str
    revenue_monthly: list[float]  # DKKk
    receivable_days: int = Field(30, ge=0, le=180)


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)

    revenue_streams: list[RevenueStream] = Field(default_factory=list)
    total_opex_monthly: list[float] = Field(default_factory=list)
    payable_days: int = Field(30, ge=0, le=180)

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for i, s in enumerate(self.revenue_streams):
            if len(s.revenue_monthly) != n:
                raise ValueError(
                    f"revenue_streams[{i}].revenue_monthly length "
                    f"{len(s.revenue_monthly)} != periods {n}"
                )
        if self.total_opex_monthly and len(self.total_opex_monthly) != n:
            raise ValueError(
                f"total_opex_monthly length {len(self.total_opex_monthly)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    total_receivables: list[float]
    total_payables: list[float]
    working_capital_balance: list[float]
    working_capital_change: list[float]
    total_revenue_received: list[float]
    opex_paid: list[float]


# ============================================================================
# CALCULATION
# ============================================================================

def _delay_months(days: int) -> int:
    """Convert day-count to month lag, minimum 1."""
    return max(1, round(days / 30))


def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods

    # -- Receivables per stream, then aggregate --
    total_receivables = [0.0] * n
    total_revenue_received = [0.0] * n

    for stream in inputs.revenue_streams:
        delay = _delay_months(stream.receivable_days)
        rev = stream.revenue_monthly
        received = [0.0] * n
        closing = [0.0] * n

        for p in range(n):
            received[p] = rev[p - delay] if p >= delay else 0.0
            opening = closing[p - 1] if p > 0 else 0.0
            closing[p] = opening + rev[p] - received[p]

        for p in range(n):
            total_receivables[p] += closing[p]
            total_revenue_received[p] += received[p]

    # -- Payables --
    total_payables = [0.0] * n
    opex_paid = [0.0] * n

    opex = inputs.total_opex_monthly if inputs.total_opex_monthly else [0.0] * n
    delay = _delay_months(inputs.payable_days)

    for p in range(n):
        opex_paid[p] = opex[p - delay] if p >= delay else 0.0
        opening = total_payables[p - 1] if p > 0 else 0.0
        total_payables[p] = opening + opex[p] - opex_paid[p]

    # -- Working capital --
    wc_balance = [total_receivables[p] - total_payables[p] for p in range(n)]
    wc_change = [
        wc_balance[p] - (wc_balance[p - 1] if p > 0 else 0.0)
        for p in range(n)
    ]

    return Outputs(
        total_receivables=total_receivables,
        total_payables=total_payables,
        working_capital_balance=wc_balance,
        working_capital_change=wc_change,
        total_revenue_received=total_revenue_received,
        opex_paid=opex_paid,
    )


# ============================================================================
# EXCEL FORMULAS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows."""
    return {
        "total_receivables": "=SUM(stream_receivables)",
        "total_payables": "=opening_payable + opex - opex_paid",
        "working_capital_balance": "=total_receivables - total_payables",
        "working_capital_change": "=wc_balance[p] - wc_balance[p-1]",
        "total_revenue_received": "=revenue[p - delay_months]",
        "opex_paid": "=opex[p - delay_months]",
    }
