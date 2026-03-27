"""
MODULE_ID:    SHL_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-18

Shareholder loan (SHL) schedule.

Models a subordinated loan from the project sponsor, typically sized as a
percentage of total equity contributed. Two modes:

  accrued=True  (default): interest compounds on the balance (PIK).
                           No cash interest is paid; interest accrues to the
                           loan balance until repayment.
  accrued=False:           interest is paid cash each period, balance stays
                           flat until repayment.

Repayment: when a repayment_schedule is provided, principal is repaid per that
schedule. Otherwise the full balance is repaid in the final period.

Balance mechanics (accrued mode):
    opening[p]  = closing[p-1]
    interest[p] = opening[p] × monthly_margin
    closing[p]  = opening[p] + interest[p] − repayment[p]

Balance mechanics (cash-pay mode):
    opening[p]  = closing[p-1]
    interest[p] = opening[p] × monthly_margin   (paid cash)
    closing[p]  = opening[p] − repayment[p]

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# SUB-MODELS
# ============================================================================

class SHLTranche(BaseModel):
    """One tranche of a shareholder loan."""
    name: str = Field(..., description="Tranche identifier, e.g. 'EV' or 'BESS'")

    # Sizing — exactly ONE of these two:
    fixed_amount_DKKk: Optional[float] = Field(None, ge=0)
    drawdown_schedule: Optional[list[float]] = Field(None)

    shl_pct_of_equity: Optional[float] = Field(None, ge=0, le=1.0)
    equity_contributed: Optional[list[float]] = Field(None)

    # Terms
    margin: float = Field(0.07, ge=0)
    accrued: bool = Field(True)
    repayment_schedule: Optional[list[float]] = Field(None)


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0, description="Total project life in months")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # SHL sizing
    shl_pct_of_equity: float = Field(
        0.80, ge=0, le=1.0,
        description="SHL as fraction of total equity contributed (e.g. 0.80 = 80%)"
    )
    margin: float = Field(
        0.0795, ge=0,
        description="Annual SHL interest margin (e.g. 0.0795 = 7.95%)"
    )
    accrued: bool = Field(
        True,
        description="If True, interest accrues (PIK); if False, interest is paid cash"
    )

    # Equity contributed — monthly series from which SHL is sized
    equity_contributed: list[float] = Field(
        ..., description="Monthly equity contributions DKKk (length = periods)"
    )

    # Optional repayment schedule — monthly amounts (length = periods)
    # If not provided, full balance is repaid in the last period.
    repayment_schedule: Optional[list[float]] = Field(
        None, description="Monthly SHL repayment amounts DKKk (optional)"
    )

    # Multi-tranche (overrides single-tranche fields when set)
    tranches: Optional[list[SHLTranche]] = Field(
        None, description="Multi-tranche definitions. Overrides top-level fields when set."
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        # Single-tranche validation (only when tranches not set)
        if self.tranches is None:
            if len(self.equity_contributed) != n:
                raise ValueError(
                    f"equity_contributed length {len(self.equity_contributed)} != periods {n}"
                )
            if self.repayment_schedule is not None and len(self.repayment_schedule) != n:
                raise ValueError(
                    f"repayment_schedule length {len(self.repayment_schedule)} != periods {n}"
                )
        else:
            # Multi-tranche validation
            for i, t in enumerate(self.tranches):
                has_fixed = t.fixed_amount_DKKk is not None
                has_pct = t.shl_pct_of_equity is not None
                if has_fixed == has_pct:
                    raise ValueError(
                        f"tranches[{i}]: must set exactly one of "
                        f"fixed_amount_DKKk or shl_pct_of_equity"
                    )
                if has_fixed:
                    if t.drawdown_schedule is None:
                        raise ValueError(f"tranches[{i}]: fixed_amount requires drawdown_schedule")
                    if len(t.drawdown_schedule) != n:
                        raise ValueError(f"tranches[{i}]: drawdown_schedule length != periods")
                    if abs(sum(t.drawdown_schedule) - t.fixed_amount_DKKk) > 0.01:
                        raise ValueError(f"tranches[{i}]: drawdown_schedule sum != fixed_amount")
                if has_pct:
                    if t.equity_contributed is None:
                        raise ValueError(f"tranches[{i}]: shl_pct_of_equity requires equity_contributed")
                    if len(t.equity_contributed) != n:
                        raise ValueError(f"tranches[{i}]: equity_contributed length != periods")
                if t.repayment_schedule is not None and len(t.repayment_schedule) != n:
                    raise ValueError(f"tranches[{i}]: repayment_schedule length != periods")
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]    # DKKk
    interest: list[float]           # DKKk — accrued or cash-paid
    repayment: list[float]          # DKKk — principal repaid
    closing_balance: list[float]    # DKKk

    # Cash flows (for wiring into CF_001)
    interest_cash: list[float]      # DKKk — 0 if accrued, = interest if cash-pay
    total_cash_flow: list[float]    # DKKk — repayment + interest_cash (cash out)

    # Summary
    total_interest: float
    total_repayment: float
    final_balance: float
    initial_shl: float              # = shl_pct_of_equity × sum(equity_contributed)
    fully_repaid: bool

    # Multi-tranche detail (None when single-tranche)
    tranche_outputs: Optional[list[dict]] = None


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


# ============================================================================
# CORE CALCULATION
# ============================================================================

def _run_single_tranche(
    n: int, margin: float, accrued: bool,
    shl_drawdown: list[float],
    repayment_schedule: Optional[list[float]],
) -> dict:
    """Run one SHL tranche and return arrays."""
    r = margin / 12.0
    repay_sched = list(repayment_schedule) if repayment_schedule else [0.0] * n

    opening = [0.0] * n
    interest = [0.0] * n
    repayment = [0.0] * n
    closing = [0.0] * n

    balance = 0.0
    for p in range(n):
        balance += shl_drawdown[p]
        opening[p] = balance
        interest[p] = balance * r
        if accrued:
            balance += interest[p]
        rep = min(repay_sched[p], balance) if repay_sched[p] > 0 else 0.0
        if repayment_schedule is None and p == n - 1:
            rep = balance
        repayment[p] = rep
        balance -= rep
        closing[p] = balance

    interest_cash = [0.0] * n if accrued else list(interest)
    return {
        "opening_balance": opening, "interest": interest,
        "repayment": repayment, "closing_balance": closing,
        "interest_cash": interest_cash,
        "initial_shl": sum(shl_drawdown),
    }


def calculate(inputs: Inputs) -> Outputs:
    """Build the monthly SHL schedule."""
    n = inputs.periods

    # --- Multi-tranche path ---
    if inputs.tranches is not None:
        agg_open = [0.0] * n
        agg_int = [0.0] * n
        agg_rep = [0.0] * n
        agg_close = [0.0] * n
        agg_int_cash = [0.0] * n
        tranche_details = []
        total_initial = 0.0

        for t in inputs.tranches:
            # Build drawdown for this tranche
            if t.fixed_amount_DKKk is not None:
                dd = list(t.drawdown_schedule)
            else:
                dd = [e * t.shl_pct_of_equity for e in t.equity_contributed]

            result = _run_single_tranche(n, t.margin, t.accrued, dd, t.repayment_schedule)
            total_initial += result["initial_shl"]

            for p in range(n):
                agg_open[p] += result["opening_balance"][p]
                agg_int[p] += result["interest"][p]
                agg_rep[p] += result["repayment"][p]
                agg_close[p] += result["closing_balance"][p]
                agg_int_cash[p] += result["interest_cash"][p]

            tranche_details.append({
                "name": t.name,
                "opening_balance": result["opening_balance"],
                "interest": result["interest"],
                "repayment": result["repayment"],
                "closing_balance": result["closing_balance"],
                "interest_cash": result["interest_cash"],
                "initial_shl": result["initial_shl"],
            })

        total_cash = [agg_rep[p] + agg_int_cash[p] for p in range(n)]

        return Outputs(
            opening_balance=agg_open,
            interest=agg_int,
            repayment=agg_rep,
            closing_balance=agg_close,
            interest_cash=agg_int_cash,
            total_cash_flow=total_cash,
            total_interest=sum(agg_int),
            total_repayment=sum(agg_rep),
            final_balance=agg_close[-1] if agg_close else 0.0,
            initial_shl=total_initial,
            fully_repaid=agg_close[-1] < 0.01 if agg_close else True,
            tranche_outputs=tranche_details,
        )

    # --- Single-tranche path (existing logic) ---
    r = inputs.margin / 12.0
    total_equity = sum(inputs.equity_contributed)
    initial_shl = inputs.shl_pct_of_equity * total_equity

    # Build drawdown: SHL is drawn proportionally to equity contributions
    if total_equity > 1e-9:
        shl_drawdown = [
            e * inputs.shl_pct_of_equity for e in inputs.equity_contributed
        ]
    else:
        shl_drawdown = [0.0] * n

    # Repayment schedule
    if inputs.repayment_schedule is not None:
        repay_sched = list(inputs.repayment_schedule)
    else:
        # Default: full repayment in last period
        repay_sched = [0.0] * n

    opening = [0.0] * n
    interest = [0.0] * n
    repayment = [0.0] * n
    closing = [0.0] * n

    balance = 0.0
    for p in range(n):
        balance += shl_drawdown[p]
        opening[p] = balance
        interest[p] = balance * r

        if inputs.accrued:
            # PIK: interest added to balance
            balance += interest[p]
        # else: interest paid cash, balance unchanged

        # Repayment
        rep = min(repay_sched[p], balance) if repay_sched[p] > 0 else 0.0
        # Default repayment in final period if no schedule provided
        if inputs.repayment_schedule is None and p == n - 1:
            rep = balance
        repayment[p] = rep
        balance -= rep
        closing[p] = balance

    # Cash flows
    interest_cash = [0.0] * n if inputs.accrued else list(interest)
    total_cash = [repayment[p] + interest_cash[p] for p in range(n)]

    return Outputs(
        opening_balance=opening,
        interest=interest,
        repayment=repayment,
        closing_balance=closing,
        interest_cash=interest_cash,
        total_cash_flow=total_cash,
        total_interest=sum(interest),
        total_repayment=sum(repayment),
        final_balance=closing[-1] if closing else 0.0,
        initial_shl=initial_shl,
        fully_repaid=closing[-1] < 0.01 if closing else True,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "opening_balance": f"={r['closing_prev']}+{r['shl_drawdown']}",
        "interest": f"={r['opening_bal']}*{r['monthly_margin']}",
        "closing_balance_accrued": (
            f"={r['opening_bal']}+{r['interest']}-{r['repayment']}"
        ),
        "closing_balance_cash": (
            f"={r['opening_bal']}-{r['repayment']}"
        ),
    }
