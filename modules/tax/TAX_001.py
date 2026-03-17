"""
MODULE_ID:    TAX_001
VERSION:      1.1
TIER:         detailed
MARKETS:      ["DK"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Danish corporate tax calculation for renewable energy projects.

Implements:
  - Declining balance depreciation (saldometoden), 7 buckets:
      0. Hard PV CAPEX          (driftsmidler, typically 25%)
      1. Hard BESS CAPEX        (driftsmidler, typically 25%)
      2. Grid connection        (driftsmidler, typically 25%)
      3. Development            (driftsmidler, typically 25%)
      4. Land / real estate     (bygninger, typically 4%)
      5. Advisors               (driftsmidler, typically 25%)
      6. BESS repowering        (driftsmidler, typically 25%)
  - EBITDA-based interest deductibility (rentefradragsbegrænsning)
  - Tax loss carry-forward with full/partial usage limits
  - Monthly cash payment timing (month 3 = prior year, month 11 = YTD)

Backward compatible: 4-bucket inputs are automatically padded to 7.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §7.1
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from pydantic import BaseModel, Field, model_validator


N_BUCKETS = 7

BUCKET_NAMES = [
    "Hard PV CAPEX",
    "Hard BESS CAPEX",
    "Grid connection",
    "Development",
    "Land / real estate",
    "Advisors",
    "BESS repowering",
]


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    country: str = Field("DK", description="Market — used to load assumption_db")

    # Timeline
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # Monthly time series — each list must have length == periods
    ebitda: list[float] = Field(..., description="Monthly EBITDA DKKk")
    total_interest: list[float] = Field(..., description="Monthly gross interest expense DKKk")

    # Depreciation: 4 or 7 buckets, each a list of monthly capex additions (length = periods)
    # If 4 buckets provided, padded to 7 with zeros for backward compatibility.
    capex_by_bucket: list[list[float]] = Field(
        description="Monthly capex additions per bucket — 4 or 7 lists, each length = periods"
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * N_BUCKETS,
        description="Opening tax basis per bucket DKKk (4 or 7 values)"
    )

    @model_validator(mode="after")
    def _check_lengths(self):
        n = self.periods
        if len(self.ebitda) != n:
            raise ValueError(f"ebitda length {len(self.ebitda)} != periods {n}")
        if len(self.total_interest) != n:
            raise ValueError(f"total_interest length {len(self.total_interest)} != periods {n}")
        nb = len(self.capex_by_bucket)
        if nb not in (4, N_BUCKETS):
            raise ValueError(
                f"capex_by_bucket must have 4 or {N_BUCKETS} bucket lists, got {nb}"
            )
        for i, b in enumerate(self.capex_by_bucket):
            if len(b) != n:
                raise ValueError(f"capex_by_bucket[{i}] length {len(b)} != periods {n}")
        # Pad 4 → 7
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
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    tax_depreciation: list[float]       # DKKk — monthly allocation of annual depreciation
    deductible_interest: list[float]    # DKKk — monthly deductible interest
    non_deductible_interest: list[float]  # DKKk — non-deductible interest
    tax_charge_accrued: list[float]     # DKKk — monthly accrual (annual charge / 12)
    tax_paid: list[float]               # DKKk — cash tax payments

    # Annual arrays (one entry per calendar year spanned)
    annual_taxable_income: list[float]
    annual_tax_charge: list[float]
    annual_depreciation: list[float]
    annual_non_deductible_interest: list[float]

    # Totals
    total_tax_paid: float
    total_tax_depreciation: float
    total_non_deductible_interest: float


# ============================================================================
# ASSUMPTION DB LOADER
# ============================================================================

def get_default_assumptions(market: str) -> dict:
    db_path = Path(__file__).parents[2] / "registry" / "assumption_db" / f"{market}.json"
    if not db_path.exists():
        raise FileNotFoundError(f"No assumption file for market: {market}")
    with open(db_path) as f:
        data = json.load(f)
    return data["tax"]


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    """
    Returns OrderedDict mapping calendar_year -> list of 0-based period indices.
    Each period is assigned to the calendar year its start date falls in.
    """
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        cal_year = start_year + offset // 12
        groups.setdefault(cal_year, []).append(p)
    return groups


def _payment_periods(
    year_groups: OrderedDict,
    start_year: int,
    start_month: int,
    periods: int,
    payment_month_prior: int,
    payment_month_current: int,
) -> dict:
    """
    For each fiscal year, find the two monthly period indices where tax is paid:
      current: month `payment_month_current` of the same year   (e.g. Nov = YTD prepayment)
      prior:   month `payment_month_prior`   of the *following* year (e.g. Mar = prior-year settlement)

    Returns dict: {year: {"prior": int|None, "current": int|None}}
    """
    # Build (cal_year, cal_month) -> period index lookup
    cal_to_period: dict = {}
    for p in range(periods):
        offset = start_month - 1 + p
        y = start_year + offset // 12
        m = (offset % 12) + 1
        cal_to_period[(y, m)] = p

    result = {}
    for year in year_groups:
        result[year] = {
            "current": cal_to_period.get((year, payment_month_current)),
            "prior":   cal_to_period.get((year + 1, payment_month_prior)),
        }
    return result


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

def calculate_declining_balance(
    opening_balances: list[float],
    capex_by_bucket: list[list[float]],
    rates: list[float],
    year_groups: OrderedDict,
    periods: int,
) -> tuple[list[float], list[float]]:
    """
    Saldometoden: depreciation = rate[b] × (opening balance + year capex additions).
    Closing balance = (opening + additions) × (1 − rate[b]).

    `rates` is a list of per-bucket depreciation rates (length = N_BUCKETS).

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
        basis = [balances[b] + year_capex[b] for b in range(nb)]
        year_total = sum(basis[b] * rates[b] for b in range(nb))
        for b in range(nb):
            balances[b] = max(0.0, basis[b] * (1.0 - rates[b]))
        annual_dep.append(year_total)

    monthly_dep = _spread_annual_to_monthly(annual_dep, year_groups, periods)
    return annual_dep, monthly_dep


def calculate_ebitda_rule(
    annual_ebitda: list[float],
    annual_interest: list[float],
    unrestricted_DKKk: float,
    ebitda_pct: float,
    inflation_rate: float,
    carryforward_years: int,
) -> tuple[list[float], list[float]]:
    """
    EBITDA-based interest deductibility (rentefradragsbegrænsning).

    Each year:
      allowed = unrestricted(inflated) + max(0, EBITDA) × ebitda_pct
      non_deductible = max(0, interest − allowed)  → added to carry-forward pool
      surplus = max(0, allowed − interest)          → draws from pool (oldest first)
      deductible = interest − non_deductible + pool_drawn

    Pool items expire after `carryforward_years` years.

    Returns:
        annual_deductible_interest
        annual_non_deductible_interest
    """
    deductible = []
    non_deductible = []
    # pool: list of (remaining_amount, expiry_year_index)
    pool: list[tuple[float, int]] = []

    for y, (ebitda_y, interest_y) in enumerate(zip(annual_ebitda, annual_interest)):
        unrestricted_y = unrestricted_DKKk * ((1.0 + inflation_rate) ** y)
        ebitda_rest = max(0.0, ebitda_y) * ebitda_pct
        allowed = unrestricted_y + ebitda_rest

        # New non-deductible this year
        non_ded_y = max(0.0, interest_y - allowed)

        # Draw surplus from pool (oldest first, skip expired)
        surplus = max(0.0, allowed - interest_y)
        pool_drawn = 0.0
        new_pool: list[tuple[float, int]] = []
        for amt, exp_y in pool:
            if exp_y <= y:
                continue  # expired — discard
            if surplus - pool_drawn > 0:
                use = min(amt, surplus - pool_drawn)
                pool_drawn += use
                if amt - use > 1e-9:
                    new_pool.append((amt - use, exp_y))
            else:
                new_pool.append((amt, exp_y))
        pool = new_pool

        if non_ded_y > 1e-9:
            pool.append((non_ded_y, y + carryforward_years))

        deductible.append(interest_y - non_ded_y + pool_drawn)
        non_deductible.append(non_ded_y)

    return deductible, non_deductible


def calculate_tax_charge(
    annual_ebitda: list[float],
    annual_depreciation: list[float],
    annual_deductible_interest: list[float],
    tax_rate: float,
    inflation_rate: float,
    loss_full_limit_DKKk: float,
    loss_partial_pct: float,
) -> tuple[list[float], list[float]]:
    """
    Taxable income and tax charge with loss carry-forward.

    Taxable income (gross) = EBITDA − depreciation − deductible interest
    Loss relief:
      - Full relief up to loss_full_limit (inflated)
      - Partial relief (loss_partial_pct) on remaining taxable income
    Loss pool: unlimited carry-forward, no expiry.

    Returns:
        annual_taxable_income_net  (after loss relief)
        annual_tax_charge
    """
    taxable_net = []
    tax_charge = []
    loss_pool = 0.0

    for y in range(len(annual_ebitda)):
        gross = annual_ebitda[y] - annual_depreciation[y] - annual_deductible_interest[y]

        if gross <= 0.0:
            loss_pool += -gross
            taxable_net.append(gross)
            tax_charge.append(0.0)
        else:
            limit_y = loss_full_limit_DKKk * ((1.0 + inflation_rate) ** y)
            full_relief = min(loss_pool, limit_y)
            remaining_income = gross - full_relief
            partial_relief = min(
                loss_pool - full_relief,
                remaining_income * loss_partial_pct,
            )
            total_relief = full_relief + partial_relief
            net = max(0.0, gross - total_relief)
            loss_pool = max(0.0, loss_pool - total_relief)
            taxable_net.append(net)
            tax_charge.append(net * tax_rate)

    return taxable_net, tax_charge


# ============================================================================
# MAIN CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly time-series Danish corporate tax calculation."""
    tax = get_default_assumptions(inputs.country)
    yg = _year_groups(inputs.periods, inputs.start_year, inputs.start_month)

    # Annual aggregations
    annual_ebitda = [
        sum(inputs.ebitda[p] for p in plist) for plist in yg.values()
    ]
    annual_interest = [
        sum(inputs.total_interest[p] for p in plist) for plist in yg.values()
    ]

    # Step 1 — Depreciation (per-bucket rates)
    if "depreciation_rates" in tax:
        dep_rates = list(tax["depreciation_rates"])
    else:
        dep_rates = [tax["depreciation_rate"]] * N_BUCKETS
    # Ensure we have exactly N_BUCKETS rates
    if len(dep_rates) < N_BUCKETS:
        dep_rates += [dep_rates[-1]] * (N_BUCKETS - len(dep_rates))

    annual_dep, monthly_dep = calculate_declining_balance(
        inputs.opening_balances,
        inputs.capex_by_bucket,
        dep_rates,
        yg,
        inputs.periods,
    )

    # Step 2 — EBITDA rule
    annual_ded_int, annual_non_ded_int = calculate_ebitda_rule(
        annual_ebitda,
        annual_interest,
        tax["unrestricted_interest_DKKk"],
        tax["ebitda_pct"],
        tax["inflation_rate"],
        tax["interest_carryforward_years"],
    )

    # Step 3 — Tax charge with loss carry-forward
    annual_taxable, annual_charge = calculate_tax_charge(
        annual_ebitda,
        annual_dep,
        annual_ded_int,
        tax["tax_rate"],
        tax["inflation_rate"],
        tax["loss_full_limit_DKKk"],
        tax["loss_partial_pct"],
    )

    # Step 4 — Payment timing
    pay_map = _payment_periods(
        yg,
        inputs.start_year,
        inputs.start_month,
        inputs.periods,
        tax["payment_month_prior"],
        tax["payment_month_current"],
    )
    tax_paid = [0.0] * inputs.periods
    ytd_frac = tax["ytd_payment_fraction"]
    for i, year in enumerate(yg.keys()):
        charge = annual_charge[i]
        pm = pay_map[year]
        if pm["current"] is not None:
            tax_paid[pm["current"]] += charge * ytd_frac
        if pm["prior"] is not None:
            tax_paid[pm["prior"]] += charge * (1.0 - ytd_frac)

    # Monthly spreads
    monthly_ded_int     = _spread_annual_to_monthly(annual_ded_int, yg, inputs.periods)
    monthly_non_ded_int = _spread_annual_to_monthly(annual_non_ded_int, yg, inputs.periods)
    monthly_charge      = _spread_annual_to_monthly(annual_charge, yg, inputs.periods)

    return Outputs(
        tax_depreciation=monthly_dep,
        deductible_interest=monthly_ded_int,
        non_deductible_interest=monthly_non_ded_int,
        tax_charge_accrued=monthly_charge,
        tax_paid=tax_paid,
        annual_taxable_income=annual_taxable,
        annual_tax_charge=annual_charge,
        annual_depreciation=annual_dep,
        annual_non_deductible_interest=annual_non_ded_int,
        total_tax_paid=sum(tax_paid),
        total_tax_depreciation=sum(monthly_dep),
        total_non_deductible_interest=sum(annual_non_ded_int),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key rows.
    refs keys: opening_bal, capex, dep_rate, ebitda, interest, unrestricted,
               ebitda_pct, tax_rate, loss_limit, loss_pct, deductible_int,
               annual_dep, taxable_income
    """
    r = refs
    return {
        "declining_balance_dep": (
            f"=({r['opening_bal']}+{r['capex']})*{r['dep_rate']}"
        ),
        "closing_balance": (
            f"=({r['opening_bal']}+{r['capex']})*(1-{r['dep_rate']})"
        ),
        "ebitda_restriction": (
            f"=MAX(0,{r['ebitda']})*{r['ebitda_pct']}"
        ),
        "allowed_interest": (
            f"={r['unrestricted']}+MAX(0,{r['ebitda']})*{r['ebitda_pct']}"
        ),
        "non_deductible_interest": (
            f"=MAX(0,{r['interest']}-{r['allowed_interest']})"
        ),
        "taxable_income_gross": (
            f"={r['ebitda']}-{r['annual_dep']}-{r['deductible_int']}"
        ),
        "tax_charge": (
            f"=MAX(0,{r['taxable_income']})*{r['tax_rate']}"
        ),
    }
