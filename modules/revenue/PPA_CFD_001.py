"""
MODULE_ID:    PPA_CFD_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Baseload PPA / Contract for Difference as financial instrument.
Settlement = (strike - wholesale) x volume x hours.  Can be positive or negative.
"""

from __future__ import annotations

import calendar
from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)

    # CfD parameters
    active: bool = False
    volume_MW: float = Field(0.0, ge=0)
    strike_price_per_MWh: float = Field(0.0, description="Base strike price DKK/MWh")
    wholesale_price_monthly: list[float] = Field(
        default_factory=list, description="Monthly wholesale price DKK/MWh"
    )
    start_period: int = Field(0, ge=0)
    end_period: Optional[int] = Field(None)
    strike_escalation_rate: float = Field(0.0, description="Annual escalation rate")
    escalation_start_year: Optional[int] = Field(None)
    hours_per_month: list[float] = Field(
        default_factory=list, description="Hours per month override"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if self.wholesale_price_monthly and len(self.wholesale_price_monthly) != n:
            raise ValueError(
                f"wholesale_price_monthly length {len(self.wholesale_price_monthly)} != periods {n}"
            )
        if self.hours_per_month and len(self.hours_per_month) != n:
            raise ValueError(
                f"hours_per_month length {len(self.hours_per_month)} != periods {n}"
            )
        if not self.wholesale_price_monthly:
            self.wholesale_price_monthly = [0.0] * n
        if self.end_period is not None and self.end_period < self.start_period:
            raise ValueError(
                f"end_period {self.end_period} < start_period {self.start_period}"
            )
        if self.escalation_start_year is None:
            self.escalation_start_year = self.start_year
        return self


# ============================================================================
# OUTPUTS
# ============================================================================

class Outputs(BaseModel):
    cfd_flag: list[float]                 # 1.0 when active
    strike_price_indexed: list[float]     # DKK/MWh
    wholesale_price: list[float]          # DKK/MWh
    settlement_per_MWh: list[float]       # DKK/MWh (strike - wholesale)
    settlement_volume_MWh: list[float]    # MWh
    settlement_value: list[float]         # DKKk

    # Lifetime scalars
    total_settlement_lifetime: float      # DKKk
    total_positive_settlement: float      # DKKk
    total_negative_settlement: float      # DKKk

    # Annual aggregation
    annual_settlement: list[float]        # DKKk per calendar year


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


def _period_year_month(start_year: int, start_month: int, p: int) -> tuple[int, int]:
    offset = start_month - 1 + p
    year = start_year + offset // 12
    month = offset % 12 + 1
    return year, month


def _escalation_factor(
    year: int,
    escalation_start_year: int,
    rate: float,
) -> float:
    years = max(0, year - escalation_start_year)
    return (1.0 + rate) ** years


def _default_hours(year: int, month: int) -> float:
    return float(calendar.monthrange(year, month)[1] * 24)


# ============================================================================
# CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    end = inputs.end_period if inputs.end_period is not None else n

    if not inputs.active:
        zeros = [0.0] * n
        yg = _year_groups(n, inputs.start_year, inputs.start_month)
        annual = [0.0] * len(yg)
        return Outputs(
            cfd_flag=zeros,
            strike_price_indexed=zeros,
            wholesale_price=list(inputs.wholesale_price_monthly),
            settlement_per_MWh=zeros,
            settlement_volume_MWh=zeros,
            settlement_value=zeros,
            total_settlement_lifetime=0.0,
            total_positive_settlement=0.0,
            total_negative_settlement=0.0,
            annual_settlement=annual,
        )

    cfd_flag = [0.0] * n
    strike_indexed = [0.0] * n
    wholesale = list(inputs.wholesale_price_monthly)
    sett_per_mwh = [0.0] * n
    sett_vol = [0.0] * n
    sett_val = [0.0] * n

    for p in range(n):
        if p < inputs.start_period or p >= end:
            continue

        cfd_flag[p] = 1.0
        year, month = _period_year_month(inputs.start_year, inputs.start_month, p)

        factor = _escalation_factor(year, inputs.escalation_start_year, inputs.strike_escalation_rate)
        strike = inputs.strike_price_per_MWh * factor
        strike_indexed[p] = strike

        if inputs.hours_per_month:
            hours = inputs.hours_per_month[p]
        else:
            hours = _default_hours(year, month)

        spread = strike - wholesale[p]
        sett_per_mwh[p] = spread
        vol = inputs.volume_MW * hours
        sett_vol[p] = vol
        sett_val[p] = spread * vol / 1000.0

    # Lifetime totals
    total = sum(sett_val)
    total_pos = sum(v for v in sett_val if v > 0.0)
    total_neg = sum(v for v in sett_val if v < 0.0)

    # Annual aggregation
    yg = _year_groups(n, inputs.start_year, inputs.start_month)
    annual = [sum(sett_val[p] for p in ps) for ps in yg.values()]

    return Outputs(
        cfd_flag=cfd_flag,
        strike_price_indexed=strike_indexed,
        wholesale_price=wholesale,
        settlement_per_MWh=sett_per_mwh,
        settlement_volume_MWh=sett_vol,
        settlement_value=sett_val,
        total_settlement_lifetime=total,
        total_positive_settlement=total_pos,
        total_negative_settlement=total_neg,
        annual_settlement=annual,
    )


# ============================================================================
# EXCEL FORMULAS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "cfd_flag": f"=IF(AND({r['period']}>={r['start_period']},{r['period']}<{r['end_period']}),1,0)",
        "strike_price_indexed": (
            f"={r['strike_base']}*(1+{r['esc_rate']})^MAX(0,{r['year']}-{r['esc_start']})"
        ),
        "settlement_per_MWh": f"={r['strike_indexed']}-{r['wholesale']}",
        "settlement_volume_MWh": f"={r['volume_mw']}*{r['hours']}*{r['flag']}",
        "settlement_value": f"={r['sett_per_mwh']}*{r['sett_vol']}/1000",
    }
