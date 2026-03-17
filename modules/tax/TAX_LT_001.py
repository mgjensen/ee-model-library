"""
MODULE_ID:    TAX_LT_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["LT"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Lithuanian corporate tax calculation for renewable energy projects.

Implements:
  - 17% corporate tax rate
  - Straight-line depreciation per asset bucket (configurable lifetimes)
  - Loss carry-forward capped at 70% of taxable income
  - Thin capitalisation rule: 4:1 D/E ratio, excess interest non-deductible
  - Payment timing: tax paid in month 6 of the following year

Source: Lithuanian Corporate Tax Law (Pelno mokescio istatymas)
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


N_BUCKETS = 7


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # Tax parameters
    tax_rate: float = Field(0.17, description="Lithuanian corporate tax rate")
    loss_cf_cap_pct: float = Field(
        0.70, ge=0, le=1.0,
        description="Max fraction of taxable income offsetable by loss c/f"
    )

    # Monthly time series
    ebitda: list[float] = Field(..., description="Monthly EBITDA DKKk")
    interest_expense: list[float] = Field(..., description="Monthly gross interest expense DKKk")

    # Depreciation buckets (4 or 7)
    capex_by_bucket: list[list[float]] = Field(
        ..., description="Monthly capex additions per bucket — 4 or 7 lists, each length = periods"
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * N_BUCKETS,
        description="Opening tax basis per bucket DKKk (4 or 7 values)"
    )
    depreciation_lifetimes: list[int] = Field(
        ..., description="Straight-line lifetime in years per bucket"
    )

    # Thin capitalisation
    thin_cap_ratio: float = Field(
        4.0, gt=0,
        description="Maximum D/E ratio for interest deductibility"
    )
    average_debt: list[float] = Field(
        default_factory=list,
        description="Monthly average debt balance for thin-cap check DKKk"
    )
    average_equity: list[float] = Field(
        default_factory=list,
        description="Monthly average equity balance for thin-cap check DKKk"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.ebitda) != n:
            raise ValueError(f"ebitda length {len(self.ebitda)} != periods {n}")
        if len(self.interest_expense) != n:
            raise ValueError(
                f"interest_expense length {len(self.interest_expense)} != periods {n}"
            )
        nb = len(self.capex_by_bucket)
        if nb not in (4, N_BUCKETS):
            raise ValueError(
                f"capex_by_bucket must have 4 or {N_BUCKETS} bucket lists, got {nb}"
            )
        for i, b in enumerate(self.capex_by_bucket):
            if len(b) != n:
                raise ValueError(f"capex_by_bucket[{i}] length {len(b)} != periods {n}")
        # Pad 4 -> 7
        if nb == 4:
            self.capex_by_bucket = list(self.capex_by_bucket) + [
                [0.0] * n for _ in range(N_BUCKETS - 4)
            ]
        nob = len(self.opening_balances)
        if nob not in (4, N_BUCKETS):
            raise ValueError(
                f"opening_balances must have 4 or {N_BUCKETS} values, got {nob}"
            )
        if nob == 4:
            self.opening_balances = list(self.opening_balances) + [0.0] * (N_BUCKETS - 4)
        ndl = len(self.depreciation_lifetimes)
        if ndl not in (4, N_BUCKETS):
            raise ValueError(
                f"depreciation_lifetimes must have 4 or {N_BUCKETS} values, got {ndl}"
            )
        if ndl == 4:
            self.depreciation_lifetimes = list(self.depreciation_lifetimes) + [
                self.depreciation_lifetimes[-1]
            ] * (N_BUCKETS - 4)
        if self.average_debt and len(self.average_debt) != n:
            raise ValueError(
                f"average_debt length {len(self.average_debt)} != periods {n}"
            )
        if self.average_equity and len(self.average_equity) != n:
            raise ValueError(
                f"average_equity length {len(self.average_equity)} != periods {n}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    tax_depreciation: list[float]        # DKKk — monthly straight-line depreciation
    deductible_interest: list[float]     # DKKk — monthly deductible interest
    non_deductible_interest: list[float] # DKKk — monthly non-deductible interest (thin-cap)
    taxable_income: list[float]          # DKKk — monthly spread of annual taxable income
    loss_carried_forward: list[float]    # DKKk — monthly spread of loss pool
    tax_charge_accrued: list[float]      # DKKk — monthly accrual
    tax_paid: list[float]               # DKKk — cash tax payments
    deferred_tax_asset: list[float]      # DKKk — loss c/f × tax_rate
    deferred_tax_liability: list[float]  # DKKk — timing differences

    # Scalar
    total_tax_paid: float


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    """Map period indices to calendar years."""
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _spread_annual_to_monthly(
    annual_values: list[float],
    year_groups: OrderedDict,
    periods: int,
) -> list[float]:
    """Evenly spread each annual value across the months in its year."""
    monthly = [0.0] * periods
    for i, year_periods in enumerate(year_groups.values()):
        if i >= len(annual_values):
            break
        if year_periods:
            amt = annual_values[i] / len(year_periods)
            for p in year_periods:
                monthly[p] = amt
    return monthly


# ============================================================================
# CORE CALCULATIONS
# ============================================================================

def _calculate_straight_line_depreciation(
    opening_balances: list[float],
    capex_by_bucket: list[list[float]],
    lifetimes: list[int],
    year_groups: OrderedDict,
    periods: int,
) -> tuple[list[float], list[float]]:
    """
    Straight-line depreciation per bucket.

    Each bucket's annual depreciation = (opening balance + year capex) / lifetime.
    Balance reduces by depreciation each year, floored at zero.

    Returns:
        annual_depreciation: one value per year
        monthly_depreciation: evenly spread across months in each year
    """
    nb = len(opening_balances)
    balances = list(opening_balances)
    annual_dep = []

    for year_periods in year_groups.values():
        year_capex = [
            sum(capex_by_bucket[b][p] for p in year_periods)
            for b in range(nb)
        ]
        year_total = 0.0
        for b in range(nb):
            basis = balances[b] + year_capex[b]
            lifetime = lifetimes[b]
            dep = basis / lifetime if lifetime > 0 else 0.0
            dep = min(dep, basis)  # cannot depreciate more than basis
            year_total += dep
            balances[b] = max(0.0, basis - dep)
        annual_dep.append(year_total)

    monthly_dep = _spread_annual_to_monthly(annual_dep, year_groups, periods)
    return annual_dep, monthly_dep


def _calculate_thin_cap(
    annual_interest: list[float],
    average_debt: list[float],
    average_equity: list[float],
    thin_cap_ratio: float,
    year_groups: OrderedDict,
) -> tuple[list[float], list[float]]:
    """
    Thin capitalisation: if avg_debt / avg_equity > thin_cap_ratio,
    excess interest is non-deductible.

    Returns:
        annual_deductible_interest
        annual_non_deductible_interest
    """
    deductible = []
    non_deductible = []

    for i, year_periods in enumerate(year_groups.values()):
        interest_y = annual_interest[i]

        if not average_debt or not average_equity:
            # No thin-cap data → all interest deductible
            deductible.append(interest_y)
            non_deductible.append(0.0)
            continue

        avg_d = sum(average_debt[p] for p in year_periods) / len(year_periods)
        avg_e = sum(average_equity[p] for p in year_periods) / len(year_periods)

        if avg_e <= 0:
            # No equity → all interest non-deductible
            deductible.append(0.0)
            non_deductible.append(interest_y)
            continue

        de_ratio = avg_d / avg_e
        if de_ratio <= thin_cap_ratio:
            deductible.append(interest_y)
            non_deductible.append(0.0)
        else:
            # Excess portion non-deductible
            allowed_debt = avg_e * thin_cap_ratio
            allowed_frac = allowed_debt / avg_d if avg_d > 0 else 0.0
            ded = interest_y * allowed_frac
            deductible.append(ded)
            non_deductible.append(interest_y - ded)

    return deductible, non_deductible


def _calculate_tax_with_loss_cf(
    annual_ebitda: list[float],
    annual_depreciation: list[float],
    annual_deductible_interest: list[float],
    tax_rate: float,
    loss_cf_cap_pct: float,
) -> tuple[list[float], list[float], list[float]]:
    """
    Taxable income and tax charge with loss carry-forward (70% cap).

    Loss relief: positive taxable income can be offset by min(loss_pool, cap_pct * taxable).
    Loss pool: unlimited carry-forward, no expiry.

    Returns:
        annual_taxable_income (after relief)
        annual_tax_charge
        annual_loss_pool (end-of-year loss pool balance)
    """
    taxable = []
    charge = []
    loss_pool_arr = []
    loss_pool = 0.0

    for y in range(len(annual_ebitda)):
        gross = annual_ebitda[y] - annual_depreciation[y] - annual_deductible_interest[y]

        if gross <= 0:
            loss_pool += -gross
            taxable.append(gross)
            charge.append(0.0)
        else:
            # Cap the offset at loss_cf_cap_pct of gross taxable
            max_offset = gross * loss_cf_cap_pct
            relief = min(loss_pool, max_offset)
            net = gross - relief
            loss_pool = max(0.0, loss_pool - relief)
            taxable.append(net)
            charge.append(max(0.0, net) * tax_rate)

        loss_pool_arr.append(loss_pool)

    return taxable, charge, loss_pool_arr


# ============================================================================
# MAIN CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly time-series Lithuanian corporate tax calculation."""
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    # Annual aggregations
    annual_ebitda = [
        sum(inputs.ebitda[p] for p in plist) for plist in yg.values()
    ]
    annual_interest = [
        sum(inputs.interest_expense[p] for p in plist) for plist in yg.values()
    ]

    # Step 1 — Straight-line depreciation
    annual_dep, monthly_dep = _calculate_straight_line_depreciation(
        inputs.opening_balances,
        inputs.capex_by_bucket,
        inputs.depreciation_lifetimes,
        yg,
        n,
    )

    # Step 2 — Thin capitalisation
    annual_ded_int, annual_non_ded_int = _calculate_thin_cap(
        annual_interest,
        inputs.average_debt,
        inputs.average_equity,
        inputs.thin_cap_ratio,
        yg,
    )

    # Step 3 — Tax charge with loss carry-forward (70% cap)
    annual_taxable, annual_charge, annual_loss_pool = _calculate_tax_with_loss_cf(
        annual_ebitda,
        annual_dep,
        annual_ded_int,
        inputs.tax_rate,
        inputs.loss_cf_cap_pct,
    )

    # Step 4 — Payment timing: pay in month 6 of following year
    tax_paid = [0.0] * n
    cal_to_period: dict = {}
    for p in range(n):
        offset = inputs.start_month - 1 + p
        y = inputs.start_year + offset // 12
        m = (offset % 12) + 1
        cal_to_period[(y, m)] = p

    for i, year in enumerate(yg.keys()):
        charge_y = annual_charge[i]
        pay_period = cal_to_period.get((year + 1, 6))
        if pay_period is not None:
            tax_paid[pay_period] += charge_y

    # Monthly spreads
    monthly_ded_int = _spread_annual_to_monthly(annual_ded_int, yg, n)
    monthly_non_ded_int = _spread_annual_to_monthly(annual_non_ded_int, yg, n)
    monthly_taxable = _spread_annual_to_monthly(annual_taxable, yg, n)
    monthly_charge = _spread_annual_to_monthly(annual_charge, yg, n)
    monthly_loss_pool = _spread_annual_to_monthly(annual_loss_pool, yg, n)

    # Deferred tax
    # DTA = loss pool × tax_rate (tax benefit of unused losses)
    deferred_tax_asset = [lp * inputs.tax_rate for lp in monthly_loss_pool]

    # DTL = cumulative accounting dep vs tax dep timing difference × tax_rate
    # For straight-line, DTL is typically zero; set to zero for this version
    deferred_tax_liability = [0.0] * n

    return Outputs(
        tax_depreciation=monthly_dep,
        deductible_interest=monthly_ded_int,
        non_deductible_interest=monthly_non_ded_int,
        taxable_income=monthly_taxable,
        loss_carried_forward=monthly_loss_pool,
        tax_charge_accrued=monthly_charge,
        tax_paid=tax_paid,
        deferred_tax_asset=deferred_tax_asset,
        deferred_tax_liability=deferred_tax_liability,
        total_tax_paid=sum(tax_paid),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key rows.
    refs keys: opening_bal, capex, lifetime, ebitda, interest,
               annual_dep, deductible_int, taxable_income, tax_rate,
               avg_debt, avg_equity, thin_cap_ratio, loss_pool, loss_cap_pct
    """
    r = refs
    return {
        "straight_line_dep": (
            f"=({r['opening_bal']}+{r['capex']})/{r['lifetime']}"
        ),
        "closing_balance": (
            f"={r['opening_bal']}+{r['capex']}"
            f"-({r['opening_bal']}+{r['capex']})/{r['lifetime']}"
        ),
        "thin_cap_check": (
            f"=IF({r['avg_debt']}/{r['avg_equity']}>{r['thin_cap_ratio']},"
            f"{r['interest']}*{r['avg_equity']}*{r['thin_cap_ratio']}/{r['avg_debt']},"
            f"{r['interest']})"
        ),
        "non_deductible_interest": (
            f"={r['interest']}-{r['deductible_int']}"
        ),
        "taxable_income_gross": (
            f"={r['ebitda']}-{r['annual_dep']}-{r['deductible_int']}"
        ),
        "loss_offset": (
            f"=MIN({r['loss_pool']},{r['taxable_income']}*{r['loss_cap_pct']})"
        ),
        "tax_charge": (
            f"=MAX(0,{r['taxable_income']})*{r['tax_rate']}"
        ),
    }
