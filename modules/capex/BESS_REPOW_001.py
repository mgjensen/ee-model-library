"""
MODULE_ID:    BESS_REPOW_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["BESS", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

BESS battery stack repowering (replacement).

Models the replacement of BESS battery stacks at a specified date after COD.
Outputs:
  - Repowering cost time-series (single-month event)
  - Pre/post-repowering operation flags
  - Tax depreciation schedule for repowering bucket (declining balance)
  - Accounting depreciation schedule (straight-line over stack lifetime)

Source: Holsted Hybrid vendor model §1.1 Input_NTD rows 462-470,
        §2.3 C_Tax rows 112-122 (tax dep), rows 24-36 (accounting dep).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

_TAX_DEP_METHODS = ("declining_balance", "straight_line")


class Inputs(BaseModel):
    active: bool = Field(False, description="Repowering enabled")
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    bess_cod_year: int = Field(..., description="BESS COD calendar year")
    bess_cod_month: int = Field(..., ge=1, le=12, description="BESS COD calendar month")
    repowering_years_after_cod: int = Field(
        ..., gt=0, description="Years after COD for repowering (e.g. 20)"
    )

    bess_capacity_MWh: float = Field(..., gt=0, description="BESS capacity in MWh")
    repowering_cost_per_MWh_DKKk: float = Field(
        ..., gt=0, description="Cost per MWh at repowering date, DKKk"
    )

    tax_depreciation_method: str = Field(
        "declining_balance",
        description="'declining_balance' or 'straight_line'"
    )
    tax_depreciation_rate: float = Field(
        0.25, gt=0, le=1.0, description="Annual tax depreciation rate"
    )
    tax_depreciation_lifetime_years: int = Field(
        25, gt=0, description="Tax depreciation lifetime in years"
    )
    accounting_lifetime_years: int = Field(
        10, gt=0, description="Accounting straight-line lifetime in years"
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.tax_depreciation_method not in _TAX_DEP_METHODS:
            raise ValueError(
                f"tax_depreciation_method must be one of {_TAX_DEP_METHODS}, "
                f"got {self.tax_depreciation_method!r}"
            )
        return self


class Outputs(BaseModel):
    repowering_month: int                           # period index (-1 if inactive/outside)
    repowering_cost_total_DKKk: float               # total cost
    repowering_cost_monthly: list[float]             # spike in repowering month
    pre_repowering_flag: list[float]                 # 1.0 before repowering
    post_repowering_flag: list[float]                # 1.0 after repowering
    tax_depreciation_monthly: list[float]            # tax dep from repowering date
    tax_asset_base_eop: list[float]                  # tax book value EoP
    accounting_depreciation_monthly: list[float]     # accounting dep (straight-line)
    accounting_asset_base_eop: list[float]           # accounting book value EoP


# ============================================================================
# HELPERS
# ============================================================================

def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _empty_outputs(n: int) -> Outputs:
    return Outputs(
        repowering_month=-1,
        repowering_cost_total_DKKk=0.0,
        repowering_cost_monthly=_zeros(n),
        pre_repowering_flag=_zeros(n),
        post_repowering_flag=_zeros(n),
        tax_depreciation_monthly=_zeros(n),
        tax_asset_base_eop=_zeros(n),
        accounting_depreciation_monthly=_zeros(n),
        accounting_asset_base_eop=_zeros(n),
    )


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods

    # Compute repowering period
    repow_year = inputs.bess_cod_year + inputs.repowering_years_after_cod
    repow_month = inputs.bess_cod_month
    repow_period = (repow_year - inputs.start_year) * 12 + (repow_month - inputs.start_month)

    if not inputs.active or repow_period >= n or repow_period < 0:
        return _empty_outputs(n)

    # Repowering cost
    total_cost = inputs.bess_capacity_MWh * inputs.repowering_cost_per_MWh_DKKk
    cost_monthly = _zeros(n)
    cost_monthly[repow_period] = total_cost

    # Operation flags
    bess_cod_period = (
        (inputs.bess_cod_year - inputs.start_year) * 12
        + (inputs.bess_cod_month - inputs.start_month)
    )
    pre_flag = _zeros(n)
    post_flag = _zeros(n)
    for p in range(n):
        if bess_cod_period <= p < repow_period:
            pre_flag[p] = 1.0
        if p >= repow_period:
            post_flag[p] = 1.0

    # Tax depreciation
    tax_dep = _zeros(n)
    tax_base = _zeros(n)
    tax_end = min(repow_period + inputs.tax_depreciation_lifetime_years * 12, n)

    if inputs.tax_depreciation_method == "declining_balance":
        base = total_cost
        for p in range(repow_period, tax_end):
            if base < 0.01:
                break
            monthly_dep = base * inputs.tax_depreciation_rate / 12.0
            tax_dep[p] = monthly_dep
            base -= monthly_dep
            tax_base[p] = base
    else:  # straight_line
        monthly_dep = total_cost / (inputs.tax_depreciation_lifetime_years * 12)
        base = total_cost
        for p in range(repow_period, tax_end):
            if base < 0.01:
                break
            dep = min(monthly_dep, base)
            tax_dep[p] = dep
            base -= dep
            tax_base[p] = base

    # Accounting depreciation (always straight-line)
    acct_dep = _zeros(n)
    acct_base = _zeros(n)
    acct_end = min(repow_period + inputs.accounting_lifetime_years * 12, n)
    acct_monthly = total_cost / (inputs.accounting_lifetime_years * 12)
    base = total_cost
    for p in range(repow_period, acct_end):
        if base < 0.01:
            break
        dep = min(acct_monthly, base)
        acct_dep[p] = dep
        base -= dep
        acct_base[p] = base

    return Outputs(
        repowering_month=repow_period,
        repowering_cost_total_DKKk=total_cost,
        repowering_cost_monthly=cost_monthly,
        pre_repowering_flag=pre_flag,
        post_repowering_flag=post_flag,
        tax_depreciation_monthly=tax_dep,
        tax_asset_base_eop=tax_base,
        accounting_depreciation_monthly=acct_dep,
        accounting_asset_base_eop=acct_base,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "repowering_cost": (
            f"=IF({r['period_ref']}={r['repow_period']},"
            f"{r['capacity']}*{r['cost_per_MWh']},0)"
        ),
        "tax_dep_declining": f"={r['prev_tax_base']}*{r['tax_rate']}/12",
        "tax_dep_straight": (
            f"=IF(AND({r['period_ref']}>={r['repow_period']},"
            f"{r['period_ref']}<{r['repow_period']}+{r['tax_lifetime']}*12),"
            f"{r['total_cost']}/({r['tax_lifetime']}*12),0)"
        ),
        "accounting_dep": (
            f"=IF(AND({r['period_ref']}>={r['repow_period']},"
            f"{r['period_ref']}<{r['repow_period']}+{r['acct_lifetime']}*12),"
            f"{r['total_cost']}/({r['acct_lifetime']}*12),0)"
        ),
        "pre_flag": (
            f"=IF(AND({r['period_ref']}>={r['cod_period']},"
            f"{r['period_ref']}<{r['repow_period']}),1,0)"
        ),
        "post_flag": f"=IF({r['period_ref']}>={r['repow_period']},1,0)",
    }
