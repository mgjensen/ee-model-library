"""
MODULE_ID:    TAX_AU_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["AU"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-30
MODIFIED:     2026-03-30

Australian corporate tax calculation for renewable energy projects.

Implements:
  - 30% corporate tax rate (configurable)
  - Straight-line depreciation per asset bucket (configurable lifetimes)
  - Loss carry-forward (100% offset, no cap — standard ATO treatment)
  - All interest fully deductible (no EBITDA cap or thin cap in v1)
  - Payment timing: prior year tax paid in configurable month (default June)

Simplified for Mulwala/Lancaster calibration. Future v2 may add:
  - Division 43 capital works deductions
  - Accelerated depreciation for small business entities
  - Thin capitalisation (Part IVA safe harbour)

Source: Income Tax Assessment Act 1997 (ITAA 1997)
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator


N_BUCKETS = 7


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    tax_rate: float = Field(0.30, description="Australian corporate tax rate")
    loss_cf_active: bool = Field(True, description="Enable loss carry-forward")

    # Monthly time series (wired by engine)
    ebitda: list[float] = Field(..., description="Monthly EBITDA (currency-k)")
    interest_expense: list[float] = Field(..., description="Monthly interest expense (currency-k)")

    # Depreciation buckets — straight-line
    capex_by_bucket: list[list[float]] = Field(
        ..., description="Per-bucket capex additions (4 or 7 lists, each length=periods)"
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * N_BUCKETS,
        description="Opening tax basis per bucket (currency-k)"
    )
    depreciation_lifetimes: list[int] = Field(
        default_factory=lambda: [30, 30, 30, 30, 30, 30, 10],
        description="Useful life per bucket in years"
    )

    tax_payment_month: int = Field(
        6, ge=1, le=12,
        description="Month (1-12) when prior year tax is paid"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.ebitda) != n:
            raise ValueError(f"ebitda length {len(self.ebitda)} != periods {n}")
        if len(self.interest_expense) != n:
            raise ValueError(f"interest_expense length {len(self.interest_expense)} != periods {n}")
        nb = len(self.capex_by_bucket)
        if nb not in (4, N_BUCKETS):
            raise ValueError(f"capex_by_bucket must have 4 or {N_BUCKETS} lists, got {nb}")
        for i, b in enumerate(self.capex_by_bucket):
            if len(b) != n:
                raise ValueError(f"capex_by_bucket[{i}] length {len(b)} != periods {n}")
        if nb == 4:
            self.capex_by_bucket = list(self.capex_by_bucket) + [
                [0.0] * n for _ in range(N_BUCKETS - 4)
            ]
        nob = len(self.opening_balances)
        if nob not in (4, N_BUCKETS):
            raise ValueError(f"opening_balances must have 4 or {N_BUCKETS} values, got {nob}")
        if nob == 4:
            self.opening_balances = list(self.opening_balances) + [0.0] * (N_BUCKETS - 4)
        ndl = len(self.depreciation_lifetimes)
        if ndl not in (4, N_BUCKETS):
            raise ValueError(f"depreciation_lifetimes must have 4 or {N_BUCKETS} values, got {ndl}")
        if ndl == 4:
            self.depreciation_lifetimes = list(self.depreciation_lifetimes) + [
                self.depreciation_lifetimes[-1]
            ] * (N_BUCKETS - 4)
        return self


class Outputs(BaseModel):
    tax_depreciation: list[float]
    deductible_interest: list[float]
    non_deductible_interest: list[float]
    taxable_income: list[float]
    loss_carried_forward: list[float]
    tax_charge_accrued: list[float]
    tax_paid: list[float]
    total_tax_paid: float


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


def _spread_annual_to_monthly(
    annual_values: list[float],
    year_groups: OrderedDict,
    periods: int,
) -> list[float]:
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
            dep = min(dep, basis)
            year_total += dep
            balances[b] = max(0.0, basis - dep)
        annual_dep.append(year_total)

    monthly_dep = _spread_annual_to_monthly(annual_dep, year_groups, periods)
    return annual_dep, monthly_dep


def _calculate_tax_with_loss_cf(
    annual_ebitda: list[float],
    annual_depreciation: list[float],
    annual_interest: list[float],
    tax_rate: float,
    loss_cf_active: bool,
) -> tuple[list[float], list[float], list[float]]:
    taxable = []
    charge = []
    loss_pool_arr = []
    loss_pool = 0.0

    for y in range(len(annual_ebitda)):
        gross = annual_ebitda[y] - annual_depreciation[y] - annual_interest[y]

        if gross <= 0:
            if loss_cf_active:
                loss_pool += -gross
            taxable.append(gross)
            charge.append(0.0)
        else:
            if loss_cf_active:
                relief = min(loss_pool, gross)
                net = gross - relief
                loss_pool = max(0.0, loss_pool - relief)
            else:
                net = gross
            taxable.append(net)
            charge.append(max(0.0, net) * tax_rate)

        loss_pool_arr.append(loss_pool)

    return taxable, charge, loss_pool_arr


# ============================================================================
# MAIN CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

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
        yg, n,
    )

    # Step 2 — All interest is deductible (no cap in AU v1)
    annual_ded_int = list(annual_interest)

    # Step 3 — Tax charge with loss carry-forward
    annual_taxable, annual_charge, annual_loss_pool = _calculate_tax_with_loss_cf(
        annual_ebitda, annual_dep, annual_ded_int,
        inputs.tax_rate, inputs.loss_cf_active,
    )

    # Step 4 — Payment timing
    tax_paid = [0.0] * n
    cal_to_period: dict = {}
    for p in range(n):
        offset = inputs.start_month - 1 + p
        y = inputs.start_year + offset // 12
        m = (offset % 12) + 1
        cal_to_period[(y, m)] = p

    for i, year in enumerate(yg.keys()):
        charge_y = annual_charge[i]
        pay_period = cal_to_period.get((year + 1, inputs.tax_payment_month))
        if pay_period is not None:
            tax_paid[pay_period] += charge_y

    # Monthly spreads
    monthly_ded_int = _spread_annual_to_monthly(annual_ded_int, yg, n)
    monthly_non_ded = [0.0] * n
    monthly_taxable = _spread_annual_to_monthly(annual_taxable, yg, n)
    monthly_charge = _spread_annual_to_monthly(annual_charge, yg, n)
    monthly_loss_pool = _spread_annual_to_monthly(annual_loss_pool, yg, n)

    return Outputs(
        tax_depreciation=monthly_dep,
        deductible_interest=monthly_ded_int,
        non_deductible_interest=monthly_non_ded,
        taxable_income=monthly_taxable,
        loss_carried_forward=monthly_loss_pool,
        tax_charge_accrued=monthly_charge,
        tax_paid=tax_paid,
        total_tax_paid=sum(tax_paid),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "straight_line_dep": f"=({r['opening_bal']}+{r['capex']})/{r['lifetime']}",
        "closing_balance": f"={r['opening_bal']}+{r['capex']}-({r['opening_bal']}+{r['capex']})/{r['lifetime']}",
        "taxable_income_gross": f"={r['ebitda']}-{r['annual_dep']}-{r['interest']}",
        "tax_charge": f"=MAX(0,{r['taxable_income']})*{r['tax_rate']}",
    }
