"""
MODULE_ID:    TAX_DE_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DE"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

German dual-layer corporate tax.

Models:
  1. Körperschaftsteuer (corporate income tax) at 15% + 5.5% solidarity surcharge
  2. Gewerbesteuer (trade tax) with municipal multiplier
  3. Interest deduction limits (30% EBITDA cap, EUR 3m threshold)
  4. Trade tax add-backs (25% interest, 50% land lease)
  5. Tax loss carry forward with EUR 1m + 60% restriction
  6. Tax depreciation: declining balance and straight-line per asset bucket

Currency: EUR thousands (EURk) for German projects.

Source: Holsted Hybrid vendor model §2.3 C_Tax rows 166-249.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional
from pydantic import BaseModel, Field, model_validator


N_BUCKETS = 7


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int = Field(...)
    start_month: int = Field(..., ge=1, le=12)

    # Time-series (wired by engine)
    ebitda: list[float] = Field(..., description="Monthly EBITDA EURk")
    total_interest: list[float] = Field(..., description="Monthly interest expense EURk")
    land_lease_expense: list[float] = Field(
        default_factory=list, description="Monthly land lease EURk"
    )

    # Depreciation
    capex_by_bucket: list[list[float]] = Field(
        ..., description="Per-bucket capex additions (4 or 7 lists, each length=periods)"
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * N_BUCKETS,
        description="Opening tax basis per bucket EURk"
    )

    # Corporate tax
    cit_rate: float = Field(0.15, description="Körperschaftsteuer rate")
    solidarity_surcharge: float = Field(0.055, description="Solidaritätszuschlag on CIT")

    # Interest deduction limits
    ebitda_interest_cap_pct: float = Field(0.30, description="Max interest deduction as % of EBITDA")
    interest_deduction_threshold_EURk: float = Field(
        3_000.0, description="Fixed annual threshold EURk"
    )
    interest_cfwd_max_years: int = Field(5, ge=1, description="Unutilized interest expiry years")

    # Tax loss
    tax_loss_cfwd_active: bool = Field(True)
    tax_loss_cfwd_max_years: int = Field(99, ge=1, description="Max carry forward years")
    tax_loss_fixed_threshold_EURk: float = Field(
        1_000.0, description="Fully deductible loss amount per year EURk"
    )
    tax_loss_above_threshold_pct: float = Field(
        0.60, ge=0, le=1.0, description="% of losses above threshold deductible"
    )

    # Trade tax
    trade_tax_active: bool = Field(True)
    trade_tax_interest_addback_pct: float = Field(0.25)
    trade_tax_land_lease_addback_pct: float = Field(0.50)
    trade_tax_allowance_EURk: float = Field(24.5, description="Freibetrag per SPV EURk")
    trade_tax_base_rate: float = Field(0.035, description="Base rate 3.5%")
    trade_tax_multiplier: float = Field(4.0, description="Municipal multiplier / 100")

    # Payment timing
    tax_payment_month: int = Field(6, ge=1, le=12)

    # Depreciation per bucket
    depreciation_rates: list[float] = Field(
        default_factory=lambda: [0.25] * N_BUCKETS
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for name, arr in [("ebitda", self.ebitda), ("total_interest", self.total_interest)]:
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        if self.land_lease_expense and len(self.land_lease_expense) != n:
            raise ValueError(
                f"land_lease_expense length {len(self.land_lease_expense)} != periods {n}"
            )
        if not self.land_lease_expense:
            self.land_lease_expense = [0.0] * n
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
        if nob < N_BUCKETS:
            self.opening_balances = list(self.opening_balances) + [0.0] * (N_BUCKETS - nob)
        if len(self.depreciation_rates) < N_BUCKETS:
            self.depreciation_rates = list(self.depreciation_rates) + [
                self.depreciation_rates[-1]
            ] * (N_BUCKETS - len(self.depreciation_rates))
        return self


class Outputs(BaseModel):
    # Tax depreciation
    tax_depreciation: list[float]         # monthly total depreciation

    # Interest deduction
    deductible_interest: list[float]
    non_deductible_interest: list[float]

    # Corporate tax
    corporate_tax_charge: list[float]     # monthly accrual

    # Trade tax
    trade_tax_charge: list[float]         # monthly accrual

    # Combined
    tax_charge_accrued: list[float]       # CIT + trade (monthly accrual)
    tax_paid: list[float]                 # cash payment (timing adjusted)

    # Annual
    annual_corporate_tax: list[float]
    annual_trade_tax: list[float]
    annual_total_tax: list[float]

    # Scalars
    effective_tax_rate: float
    total_lifetime_tax: float


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


def _spread_annual(annual: list[float], yg: OrderedDict, n: int) -> list[float]:
    monthly = [0.0] * n
    for i, yp in enumerate(yg.values()):
        if i >= len(annual):
            break
        if yp:
            amt = annual[i] / len(yp)
            for p in yp:
                monthly[p] = amt
    return monthly


def _calc_depreciation(
    opening: list[float],
    capex_by_bucket: list[list[float]],
    rates: list[float],
    yg: OrderedDict,
    n: int,
) -> tuple[list[float], list[float]]:
    """Declining balance depreciation per bucket. Returns (annual, monthly)."""
    nb = len(opening)
    balances = list(opening)
    annual = []
    for yp in yg.values():
        year_capex = [sum(capex_by_bucket[b][p] for p in yp) for b in range(nb)]
        basis = [balances[b] + year_capex[b] for b in range(nb)]
        total = sum(basis[b] * rates[b] for b in range(nb))
        for b in range(nb):
            balances[b] = max(0.0, basis[b] * (1.0 - rates[b]))
        annual.append(total)
    monthly = _spread_annual(annual, yg, n)
    return annual, monthly


def _apply_loss_cfwd(
    annual_base: list[float],
    fixed_threshold: float,
    above_pct: float,
    active: bool,
) -> tuple[list[float], list[float]]:
    """Apply loss carry-forward with fixed + pct restriction.
    Returns (adjusted_base, loss_cfwd_eop)."""
    adjusted = []
    loss_cfwd = []
    pool = 0.0
    for gross in annual_base:
        if gross <= 0.0:
            if active:
                pool += -gross
            adjusted.append(gross)
        else:
            if active and pool > 0:
                full = min(pool, fixed_threshold)
                remaining = gross - full
                partial = min(pool - full, max(0.0, remaining) * above_pct)
                relief = full + partial
                net = max(0.0, gross - relief)
                pool = max(0.0, pool - relief)
                adjusted.append(net)
            else:
                adjusted.append(gross)
        loss_cfwd.append(pool)
    return adjusted, loss_cfwd


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    # Effective rates
    eff_cit = inputs.cit_rate * (1.0 + inputs.solidarity_surcharge)
    trade_rate = inputs.trade_tax_base_rate * inputs.trade_tax_multiplier

    # Annual aggregations
    annual_ebitda = [sum(inputs.ebitda[p] for p in yp) for yp in yg.values()]
    annual_interest = [sum(inputs.total_interest[p] for p in yp) for yp in yg.values()]
    annual_land = [sum(inputs.land_lease_expense[p] for p in yp) for yp in yg.values()]

    # Step 1: Depreciation
    annual_dep, monthly_dep = _calc_depreciation(
        inputs.opening_balances, inputs.capex_by_bucket,
        inputs.depreciation_rates, yg, n,
    )

    # Step 2: Interest deduction cap (annual)
    annual_ded = []
    annual_non_ded = []
    int_pool: list[tuple[float, int]] = []  # (amount, expiry_year_index)

    for y, (ebitda_y, interest_y) in enumerate(zip(annual_ebitda, annual_interest)):
        max_ded = max(
            inputs.interest_deduction_threshold_EURk,
            max(0.0, ebitda_y) * inputs.ebitda_interest_cap_pct,
        )
        non_ded = max(0.0, interest_y - max_ded)

        # Draw surplus from pool
        surplus = max(0.0, max_ded - interest_y)
        drawn = 0.0
        new_pool: list[tuple[float, int]] = []
        for amt, exp in int_pool:
            if exp <= y:
                continue
            if surplus - drawn > 0:
                use = min(amt, surplus - drawn)
                drawn += use
                if amt - use > 1e-9:
                    new_pool.append((amt - use, exp))
            else:
                new_pool.append((amt, exp))
        int_pool = new_pool
        if non_ded > 1e-9:
            int_pool.append((non_ded, y + inputs.interest_cfwd_max_years))

        annual_ded.append(interest_y - non_ded + drawn)
        annual_non_ded.append(non_ded)

    # Step 3: Corporate tax
    cit_base = [
        annual_ebitda[y] - annual_dep[y] - annual_ded[y]
        for y in range(len(annual_ebitda))
    ]
    adj_cit, cit_loss = _apply_loss_cfwd(
        cit_base, inputs.tax_loss_fixed_threshold_EURk,
        inputs.tax_loss_above_threshold_pct, inputs.tax_loss_cfwd_active,
    )
    annual_cit = [max(0.0, v) * eff_cit for v in adj_cit]

    # Step 4: Trade tax
    annual_tt = [0.0] * len(annual_ebitda)
    tt_loss_cfwd: list[float] = []
    if inputs.trade_tax_active:
        tt_base_raw = []
        for y in range(len(annual_ebitda)):
            pre_tax = annual_ebitda[y] - annual_dep[y]
            addback = (
                annual_interest[y] * inputs.trade_tax_interest_addback_pct
                + annual_land[y] * inputs.trade_tax_land_lease_addback_pct
            )
            tt_base_raw.append(pre_tax + addback - inputs.trade_tax_allowance_EURk)

        adj_tt, tt_loss_cfwd = _apply_loss_cfwd(
            tt_base_raw, inputs.tax_loss_fixed_threshold_EURk,
            inputs.tax_loss_above_threshold_pct, inputs.tax_loss_cfwd_active,
        )
        annual_tt = [max(0.0, v) * trade_rate for v in adj_tt]

    # Step 5: Total + timing
    annual_total = [annual_cit[y] + annual_tt[y] for y in range(len(annual_ebitda))]

    monthly_cit = _spread_annual(annual_cit, yg, n)
    monthly_tt = _spread_annual(annual_tt, yg, n)
    monthly_total = [monthly_cit[p] + monthly_tt[p] for p in range(n)]
    monthly_ded = _spread_annual(annual_ded, yg, n)
    monthly_non_ded = _spread_annual(annual_non_ded, yg, n)

    # Payment timing: pay in tax_payment_month of following year
    tax_paid = [0.0] * n
    cal_to_period: dict = {}
    for p in range(n):
        offset = inputs.start_month - 1 + p
        y = inputs.start_year + offset // 12
        m = (offset % 12) + 1
        cal_to_period[(y, m)] = p

    for i, year in enumerate(yg.keys()):
        charge = annual_total[i]
        pay_p = cal_to_period.get((year + 1, inputs.tax_payment_month))
        if pay_p is not None:
            tax_paid[pay_p] += charge

    # Effective rate
    total_ebt = sum(annual_ebitda[y] - annual_dep[y] - annual_ded[y]
                    for y in range(len(annual_ebitda)))
    total_tax = sum(annual_total)
    eff_rate = total_tax / total_ebt if abs(total_ebt) > 1e-9 else 0.0

    return Outputs(
        tax_depreciation=monthly_dep,
        deductible_interest=monthly_ded,
        non_deductible_interest=monthly_non_ded,
        corporate_tax_charge=monthly_cit,
        trade_tax_charge=monthly_tt,
        tax_charge_accrued=monthly_total,
        tax_paid=tax_paid,
        annual_corporate_tax=annual_cit,
        annual_trade_tax=annual_tt,
        annual_total_tax=annual_total,
        effective_tax_rate=eff_rate,
        total_lifetime_tax=total_tax,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "depreciation": f"={r['basis']}*{r['dep_rate']}",
        "interest_cap": f"=MAX({r['threshold']},{r['ebitda']}*{r['cap_pct']})",
        "corporate_tax": f"=MAX(0,{r['adj_cit_base']})*{r['eff_cit_rate']}",
        "trade_tax_addback": (
            f"={r['interest']}*{r['addback_pct']}"
            f"+{r['land_lease']}*{r['ll_addback_pct']}"
        ),
        "trade_tax": f"=MAX(0,{r['adj_tt_base']})*{r['base_rate']}*{r['multiplier']}",
        "total_tax": f"={r['corporate_tax']}+{r['trade_tax']}",
    }
