"""
MODULE_ID:    MRA_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]

Maintenance Reserve Account (MRA).

Periodic cash contributions to a ring-fenced reserve account that funds future
maintenance capex. The MRA is typically required by senior lenders:

    1. No activity before funding_start_period.
    2. At funding_start_period, fund up to target_balance.
    3. When maintenance_capex[p] > 0, release min(capex, balance) from the MRA.
    4. After a release, top up to target_balance in the same period.

Net cash impact = −funding + release (negative = cash out).

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int = Field(2025)
    start_month: int = Field(1, ge=1, le=12)

    # MRA parameters
    active: bool = Field(True)
    target_balance: float = Field(0.0, ge=0, description="Target MRA balance DKKk")
    funding_start_period: int = Field(0, ge=0, description="Period when MRA funding begins")

    # Monthly maintenance capex (releases from MRA)
    maintenance_capex: list[float] = Field(
        default_factory=list,
        description="Monthly maintenance spend DKKk (length = periods if provided)"
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.maintenance_capex and len(self.maintenance_capex) != self.periods:
            raise ValueError(
                f"maintenance_capex length {len(self.maintenance_capex)} != periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    balance: list[float]
    funding: list[float]
    release: list[float]
    net_cash_impact: list[float]

    # Summaries
    total_funding: float
    total_release: float


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly MRA schedule."""
    n = inputs.periods

    # Inactive → all zeros
    if not inputs.active or inputs.target_balance == 0.0:
        z = [0.0] * n
        return Outputs(
            balance=z, funding=z, release=z, net_cash_impact=z,
            total_funding=0.0, total_release=0.0,
        )

    capex = inputs.maintenance_capex if inputs.maintenance_capex else [0.0] * n
    target = inputs.target_balance

    balance = [0.0] * n
    funding = [0.0] * n
    release = [0.0] * n
    net_cash = [0.0] * n

    bal = 0.0
    for p in range(n):
        if p < inputs.funding_start_period:
            balance[p] = 0.0
            continue

        # Release for maintenance capex
        rel = min(capex[p], bal) if capex[p] > 0 else 0.0
        release[p] = rel
        bal -= rel

        # Fund up to target
        shortfall = max(0.0, target - bal)
        funding[p] = shortfall
        bal += shortfall

        balance[p] = bal
        net_cash[p] = -funding[p] + release[p]

    return Outputs(
        balance=balance,
        funding=funding,
        release=release,
        net_cash_impact=net_cash,
        total_funding=sum(funding),
        total_release=sum(release),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "release": f"=MIN({r['maintenance_capex']},{r['prev_balance']})",
        "funding": f"=MAX(0,{r['target']}-({r['prev_balance']}-{r['release']}))",
        "balance": f"={r['prev_balance']}-{r['release']}+{r['funding']}",
        "net_cash_impact": f"=-{r['funding']}+{r['release']}",
    }
