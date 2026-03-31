"""
MODULE_ID:    TAX_AU_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["AU"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-30
MODIFIED:     2026-04-01

Australian corporate tax calculation for renewable energy projects.

Implements:
  - 30% corporate tax rate (configurable)
  - Declining balance depreciation per asset bucket (configurable rates)
  - Loss carry-forward: unlimited, no threshold, no partial restriction
  - All interest fully deductible (no EBITDA cap or thin cap)
  - PAYG quarterly instalments: Oct, Jan, Apr, Jul = prior FY tax / 4
  - First year: no PAYG (paid on assessment)

AU FY runs Jul-Jun. Quarterly PAYG instalments fall in months 10, 1, 4, 7
(Oct, Jan, Apr, Jul of the calendar year).

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

    # Time-series (wired by engine)
    ebitda: list[float] = Field(..., description="Monthly EBITDA (currency-k)")
    total_interest: list[float] = Field(..., description="Monthly interest expense (currency-k)")

    # Depreciation buckets — declining balance
    capex_by_bucket: list[list[float]] = Field(
        ..., description="Per-bucket capex additions (up to 7 lists, each length=periods)"
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * N_BUCKETS,
        description="Opening tax basis per bucket (currency-k)"
    )
    depreciation_rates: list[float] = Field(
        default_factory=lambda: [0.25] * N_BUCKETS,
        description="Declining balance rate per bucket"
    )

    tax_rate: float = Field(0.30, description="Australian corporate tax rate")
    loss_cfwd_active: bool = Field(True, description="Enable loss carry-forward")

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for name, arr in [("ebitda", self.ebitda), ("total_interest", self.total_interest)]:
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        nb = len(self.capex_by_bucket)
        if nb > N_BUCKETS:
            raise ValueError(f"capex_by_bucket must have <= {N_BUCKETS} lists, got {nb}")
        for i, b in enumerate(self.capex_by_bucket):
            if len(b) != n:
                raise ValueError(f"capex_by_bucket[{i}] length {len(b)} != periods {n}")
        if nb < N_BUCKETS:
            self.capex_by_bucket = list(self.capex_by_bucket) + [
                [0.0] * n for _ in range(N_BUCKETS - nb)
            ]
        nob = len(self.opening_balances)
        if nob < N_BUCKETS:
            self.opening_balances = list(self.opening_balances) + [0.0] * (N_BUCKETS - nob)
        ndr = len(self.depreciation_rates)
        if ndr < N_BUCKETS:
            self.depreciation_rates = list(self.depreciation_rates) + [
                self.depreciation_rates[-1]
            ] * (N_BUCKETS - ndr)
        return self


class Outputs(BaseModel):
    tax_depreciation: list[float]          # monthly total depreciation
    corporate_tax_charge: list[float]      # monthly accrual
    tax_paid: list[float]                  # quarterly PAYG
    annual_taxable_income: list[float]
    annual_tax_charge: list[float]
    annual_loss_pool: list[float]
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


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    # Annual aggregations
    annual_ebitda = [sum(inputs.ebitda[p] for p in yp) for yp in yg.values()]
    annual_interest = [sum(inputs.total_interest[p] for p in yp) for yp in yg.values()]

    # Step 1: Declining balance depreciation
    annual_dep, monthly_dep = _calc_depreciation(
        inputs.opening_balances, inputs.capex_by_bucket,
        inputs.depreciation_rates, yg, n,
    )

    # Step 2: Taxable income = EBITDA - depreciation - interest (fully deductible)
    ny = len(annual_ebitda)
    annual_taxable = []
    annual_charge = []
    annual_loss = []
    loss_pool = 0.0

    for y in range(ny):
        gross = annual_ebitda[y] - annual_dep[y] - annual_interest[y]
        if gross <= 0:
            if inputs.loss_cfwd_active:
                loss_pool += -gross
            annual_taxable.append(gross)
            annual_charge.append(0.0)
        else:
            if inputs.loss_cfwd_active and loss_pool > 0:
                relief = min(loss_pool, gross)
                net = gross - relief
                loss_pool = max(0.0, loss_pool - relief)
            else:
                net = gross
            annual_taxable.append(net)
            annual_charge.append(max(0.0, net) * inputs.tax_rate)
        annual_loss.append(loss_pool)

    # Step 3: Monthly accrual spread
    monthly_charge = _spread_annual(annual_charge, yg, n)

    # Step 4: PAYG quarterly timing
    # AU FY = Jul-Jun. PAYG instalments in months 10 (Oct), 1 (Jan), 4 (Apr), 7 (Jul)
    # = prior FY tax / 4. First year: no PAYG.
    tax_paid = [0.0] * n
    payg_months = [10, 1, 4, 7]

    # Build calendar lookup: (year, month) -> period index
    cal_to_period: dict = {}
    for p in range(n):
        offset = inputs.start_month - 1 + p
        y = inputs.start_year + offset // 12
        m = (offset % 12) + 1
        cal_to_period[(y, m)] = p

    # Map year_groups index to AU FY
    # AU FY ending June Y covers Jul(Y-1) to Jun(Y)
    # Calendar year Y in year_groups roughly maps to FY ending Jun(Y+1) for Jan-Jun
    # and FY ending Jun(Y+1) for Jul-Dec
    # Simplification: PAYG for year-index y is paid in the following calendar year
    years = list(yg.keys())
    for y_idx in range(ny):
        if y_idx == 0:
            continue  # First year: no PAYG (paid on assessment)
        charge = annual_charge[y_idx]
        if charge <= 0:
            continue
        quarterly = charge / 4.0
        pay_year = years[y_idx] + 1
        for m in payg_months:
            pay_p = cal_to_period.get((pay_year, m))
            if pay_p is not None:
                tax_paid[pay_p] += quarterly

    # Effective rate
    total_ebt = sum(annual_ebitda[y] - annual_dep[y] - annual_interest[y] for y in range(ny))
    total_tax = sum(annual_charge)
    eff_rate = total_tax / total_ebt if abs(total_ebt) > 1e-9 else 0.0

    return Outputs(
        tax_depreciation=monthly_dep,
        corporate_tax_charge=monthly_charge,
        tax_paid=tax_paid,
        annual_taxable_income=annual_taxable,
        annual_tax_charge=annual_charge,
        annual_loss_pool=annual_loss,
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
        "closing_balance": f"={r['basis']}*(1-{r['dep_rate']})",
        "taxable_income": f"={r['ebitda']}-{r['annual_dep']}-{r['interest']}",
        "tax_charge": f"=MAX(0,{r['taxable_income']})*{r['tax_rate']}",
        "payg_quarterly": f"={r['prior_year_tax']}/4",
    }
