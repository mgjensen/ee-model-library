"""
MODULE_ID:    REV_003
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "SE", "PL", "*"]
TECHNOLOGIES: ["WIND"]

Wind revenue calculation — sub-sections A through H per EE_MODEL_BUILD_SPEC.md §5.3.
Every line item is a named output field; no opaque totals.

Sub-sections:
  A  General          — year fraction, production pass-through
  B  Price curves     — indexation, inflated wholesale & capture
  C  Spot revenue     — net production × capture price
  D  PPA revenue      — 5 slots: contracted vol, fixed payment, settlement,
                        net settlement, annual capture compensation
  E  GoO revenue      — residual volume × GoO price
  F  Production costs — TSO / DSO / Nord Pool tariffs, PPA management
  G  Balancing costs  — imbalance tariff × production
  H  Summary          — gross revenue, total costs, net revenue

Source: EE_MODEL_BUILD_SPEC.md v2.0 §5.3
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# COMPONENT SUB-MODELS
# ============================================================================

class PPASlot(BaseModel):
    """Parameters for one PPA contract slot."""
    active: bool = False
    ppa_price_DKK_per_MWh: float = Field(0.0, ge=0)
    # Monthly contracted volume (MWh) — length must equal periods when active.
    # Zero outside the PPA active window.
    contracted_volume: list[float] = Field(default_factory=list)
    # Capture compensation: if annual vol-weighted avg capture < breakeven,
    # producer receives compensation settled in settlement_month of following year.
    capture_comp_breakeven_DKK_per_MWh: float = Field(0.0, ge=0)
    capture_comp_ceiling_DKK_per_MWh: float = Field(
        0.0, ge=0, description="Max compensation per MWh (0 = uncapped)"
    )
    capture_comp_settlement_month: int = Field(6, ge=1, le=12)


class VariableTariff(BaseModel):
    """Variable cost tariff with inflation."""
    rate_DKK_per_MWh: float = Field(0.0, ge=0)
    inflation_start_year: int = Field(2025)
    indexation_start_factor: float = Field(1.0, gt=0)


# ============================================================================
# INPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    inflation_rate: float = Field(0.025, description="Annual inflation for costs and prices")

    # A — Production (from PRODUCTION tab, monthly MWh)
    net_production_MWh: list[float]

    # B — Price curves (monthly, DKK/MWh, from TIME_ASSUMPTIONS)
    wholesale_price_DKK_per_MWh: list[float]
    capture_price_DKK_per_MWh: list[float]
    price_inflation_start_year: int = Field(2025)
    price_indexation_start_factor: float = Field(1.0, gt=0)

    # D — PPA slots (always 5; inactive slots contribute zero)
    ppa_slots: list[PPASlot] = Field(
        default_factory=lambda: [PPASlot() for _ in range(5)],
        description="Exactly 5 PPA slots"
    )

    # E — GoO price (monthly, DKK/MWh)
    goo_price_DKK_per_MWh: list[float]

    # F — Production cost tariffs (DKK/MWh of net production)
    tso_tariff: VariableTariff = Field(default_factory=VariableTariff)
    dso_tariff: VariableTariff = Field(default_factory=VariableTariff)
    nordpool_tariff: VariableTariff = Field(default_factory=VariableTariff)
    # PPA management cost: DKK/MWh of total PPA contracted volume
    ppa_management_rate_DKK_per_MWh: float = Field(0.0, ge=0)

    # G — Balancing / imbalance cost
    imbalance_tariff: VariableTariff = Field(default_factory=VariableTariff)

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        for name, arr in [
            ("net_production_MWh", self.net_production_MWh),
            ("wholesale_price_DKK_per_MWh", self.wholesale_price_DKK_per_MWh),
            ("capture_price_DKK_per_MWh", self.capture_price_DKK_per_MWh),
            ("goo_price_DKK_per_MWh", self.goo_price_DKK_per_MWh),
        ]:
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        if len(self.ppa_slots) != 5:
            raise ValueError("ppa_slots must have exactly 5 entries")
        for i, slot in enumerate(self.ppa_slots):
            if slot.active and len(slot.contracted_volume) != n:
                raise ValueError(
                    f"ppa_slots[{i}].contracted_volume length "
                    f"{len(slot.contracted_volume)} != periods {n}"
                )
        return self


# ============================================================================
# OUTPUTS — every sub-section, every line item
# ============================================================================

class PPASlotOutput(BaseModel):
    """Full decomposition for one PPA slot (monthly, DKKk)."""
    contracted_volume: list[float]      # MWh
    fixed_payment: list[float]          # DKKk — contracted_volume × ppa_price / 1000
    settlement: list[float]             # DKKk — contracted_volume × wholesale_inflated / 1000
    net_settlement: list[float]         # DKKk — fixed_payment − settlement
    capture_compensation: list[float]   # DKKk — annual, settled in one month
    total_revenue: list[float]          # DKKk — net_settlement + capture_compensation


class Outputs(BaseModel):
    # --- A. General ---
    net_production_MWh: list[float]         # pass-through

    # --- B. Price curves ---
    price_indexation_factor: list[float]    # dimensionless
    wholesale_inflated: list[float]         # DKK/MWh
    capture_inflated: list[float]           # DKK/MWh

    # --- C. Spot revenue ---
    spot_revenue: list[float]               # DKKk

    # --- D. PPA revenue (5 slots, always present) ---
    ppa: list[PPASlotOutput]                # index 0–4
    total_ppa_contracted_volume: list[float]  # MWh — sum across active slots
    total_ppa_fixed_payment: list[float]    # DKKk
    total_ppa_settlement: list[float]       # DKKk
    total_ppa_net_settlement: list[float]   # DKKk
    total_ppa_capture_compensation: list[float]  # DKKk
    total_ppa_revenue: list[float]          # DKKk

    # --- E. GoO ---
    goo_volume: list[float]                 # MWh — residual after PPA
    goo_revenue: list[float]                # DKKk

    # --- F. Production costs ---
    tso_cost: list[float]                   # DKKk
    dso_cost: list[float]                   # DKKk
    nordpool_cost: list[float]              # DKKk
    ppa_management_cost: list[float]        # DKKk
    total_production_costs: list[float]     # DKKk

    # --- G. Balancing ---
    balancing_cost: list[float]             # DKKk

    # --- H. Summary ---
    gross_revenue: list[float]              # DKKk — spot + ppa + goo
    total_costs: list[float]                # DKKk — production + balancing
    net_revenue: list[float]                # DKKk — gross − total_costs

    # Annual aggregation (one entry per calendar year spanned)
    annual_net_revenue: list[float]

    # Lifetime total
    total_net_revenue: float


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


def _cal_to_period_map(periods: int, start_year: int, start_month: int) -> dict:
    """Map (calendar_year, calendar_month) → period index."""
    m = {}
    for p in range(periods):
        offset = start_month - 1 + p
        y = start_year + offset // 12
        mo = (offset % 12) + 1
        m[(y, mo)] = p
    return m


def _indexation_factor(
    year: int,
    inflation_start_year: int,
    indexation_start_factor: float,
    inflation_rate: float,
) -> float:
    years = max(0, year - inflation_start_year)
    return indexation_start_factor * (1.0 + inflation_rate) ** years


def _variable_cost_series(
    tariff: VariableTariff,
    production: list[float],
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """Monthly cost = production[p] × tariff_rate × indexation_factor / 1000 (DKKk)."""
    if tariff.rate_DKK_per_MWh == 0.0:
        return [0.0] * periods
    out = [0.0] * periods
    for year, year_periods in year_groups.items():
        factor = _indexation_factor(
            year, tariff.inflation_start_year,
            tariff.indexation_start_factor, inflation_rate
        )
        for p in year_periods:
            out[p] = production[p] * tariff.rate_DKK_per_MWh * factor / 1000.0
    return out


# ============================================================================
# SECTION D — PPA slot calculation
# ============================================================================

def _calculate_ppa_slot(
    slot: PPASlot,
    wholesale_inflated: list[float],
    capture_inflated: list[float],
    year_groups: OrderedDict,
    cal_map: dict,
    periods: int,
) -> PPASlotOutput:
    zeros = [0.0] * periods

    if not slot.active:
        return PPASlotOutput(
            contracted_volume=zeros[:],
            fixed_payment=zeros[:],
            settlement=zeros[:],
            net_settlement=zeros[:],
            capture_compensation=zeros[:],
            total_revenue=zeros[:],
        )

    vols = list(slot.contracted_volume)
    fixed_pmt = [vols[p] * slot.ppa_price_DKK_per_MWh / 1000.0 for p in range(periods)]
    settle    = [vols[p] * wholesale_inflated[p] / 1000.0 for p in range(periods)]
    net_set   = [fixed_pmt[p] - settle[p] for p in range(periods)]

    # Capture compensation — computed annually, settled in following year
    cap_comp = [0.0] * periods
    if slot.capture_comp_breakeven_DKK_per_MWh > 0:
        for year, year_periods in year_groups.items():
            annual_vol = sum(vols[p] for p in year_periods)
            if annual_vol < 1e-6:
                continue
            avg_capture = (
                sum(capture_inflated[p] * vols[p] for p in year_periods) / annual_vol
            )
            if avg_capture < slot.capture_comp_breakeven_DKK_per_MWh:
                diff = slot.capture_comp_breakeven_DKK_per_MWh - avg_capture
                if slot.capture_comp_ceiling_DKK_per_MWh > 0:
                    diff = min(diff, slot.capture_comp_ceiling_DKK_per_MWh)
                comp_DKKk = diff * annual_vol / 1000.0
                # Settle in specified month of following year
                target = (year + 1, slot.capture_comp_settlement_month)
                if target in cal_map:
                    cap_comp[cal_map[target]] += comp_DKKk

    total_rev = [net_set[p] + cap_comp[p] for p in range(periods)]

    return PPASlotOutput(
        contracted_volume=vols,
        fixed_payment=fixed_pmt,
        settlement=settle,
        net_settlement=net_set,
        capture_compensation=cap_comp,
        total_revenue=total_rev,
    )


# ============================================================================
# MAIN CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Full wind revenue calculation across sub-sections A–H."""
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)
    cal_map = _cal_to_period_map(n, inputs.start_year, inputs.start_month)
    ir = inputs.inflation_rate

    # --- A. Production ---
    net_prod = list(inputs.net_production_MWh)

    # --- B. Price indexation ---
    price_factor = [0.0] * n
    wholesale_inf = [0.0] * n
    capture_inf = [0.0] * n
    for year, year_periods in yg.items():
        f = _indexation_factor(
            year,
            inputs.price_inflation_start_year,
            inputs.price_indexation_start_factor,
            ir,
        )
        for p in year_periods:
            price_factor[p] = f
            wholesale_inf[p] = inputs.wholesale_price_DKK_per_MWh[p] * f
            capture_inf[p]   = inputs.capture_price_DKK_per_MWh[p] * f

    # --- C. Spot revenue ---
    spot_rev = [net_prod[p] * capture_inf[p] / 1000.0 for p in range(n)]

    # --- D. PPA slots ---
    ppa_outputs = [
        _calculate_ppa_slot(slot, wholesale_inf, capture_inf, yg, cal_map, n)
        for slot in inputs.ppa_slots
    ]

    total_ppa_vol    = [sum(s.contracted_volume[p] for s in ppa_outputs) for p in range(n)]
    total_ppa_fixed  = [sum(s.fixed_payment[p] for s in ppa_outputs) for p in range(n)]
    total_ppa_settle = [sum(s.settlement[p] for s in ppa_outputs) for p in range(n)]
    total_ppa_net    = [sum(s.net_settlement[p] for s in ppa_outputs) for p in range(n)]
    total_ppa_cap    = [sum(s.capture_compensation[p] for s in ppa_outputs) for p in range(n)]
    total_ppa_rev    = [total_ppa_net[p] + total_ppa_cap[p] for p in range(n)]

    # --- E. GoO ---
    goo_vol = [max(0.0, net_prod[p] - total_ppa_vol[p]) for p in range(n)]
    goo_rev = [goo_vol[p] * inputs.goo_price_DKK_per_MWh[p] / 1000.0 for p in range(n)]

    # --- F. Production costs ---
    tso_cost   = _variable_cost_series(inputs.tso_tariff,      net_prod, yg, n, ir)
    dso_cost   = _variable_cost_series(inputs.dso_tariff,      net_prod, yg, n, ir)
    np_cost    = _variable_cost_series(inputs.nordpool_tariff,  net_prod, yg, n, ir)
    ppa_mgmt   = [total_ppa_vol[p] * inputs.ppa_management_rate_DKK_per_MWh / 1000.0
                  for p in range(n)]
    total_prod_costs = [tso_cost[p] + dso_cost[p] + np_cost[p] + ppa_mgmt[p]
                        for p in range(n)]

    # --- G. Balancing ---
    balancing = _variable_cost_series(inputs.imbalance_tariff, net_prod, yg, n, ir)

    # --- H. Summary ---
    gross_rev  = [spot_rev[p] + total_ppa_rev[p] + goo_rev[p] for p in range(n)]
    total_costs = [total_prod_costs[p] + balancing[p] for p in range(n)]
    net_rev    = [gross_rev[p] - total_costs[p] for p in range(n)]

    annual_net = [sum(net_rev[p] for p in plist) for plist in yg.values()]

    return Outputs(
        # A
        net_production_MWh=net_prod,
        # B
        price_indexation_factor=price_factor,
        wholesale_inflated=wholesale_inf,
        capture_inflated=capture_inf,
        # C
        spot_revenue=spot_rev,
        # D
        ppa=ppa_outputs,
        total_ppa_contracted_volume=total_ppa_vol,
        total_ppa_fixed_payment=total_ppa_fixed,
        total_ppa_settlement=total_ppa_settle,
        total_ppa_net_settlement=total_ppa_net,
        total_ppa_capture_compensation=total_ppa_cap,
        total_ppa_revenue=total_ppa_rev,
        # E
        goo_volume=goo_vol,
        goo_revenue=goo_rev,
        # F
        tso_cost=tso_cost,
        dso_cost=dso_cost,
        nordpool_cost=np_cost,
        ppa_management_cost=ppa_mgmt,
        total_production_costs=total_prod_costs,
        # G
        balancing_cost=balancing,
        # H
        gross_revenue=gross_rev,
        total_costs=total_costs,
        net_revenue=net_rev,
        annual_net_revenue=annual_net,
        total_net_revenue=sum(net_rev),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        # B
        "price_indexation_factor": (
            f"={r['start_factor']}*(1+{r['inf_rate']})^MAX(0,{r['year']}-{r['inf_start']})"
        ),
        "wholesale_inflated": f"={r['wholesale_raw']}*{r['price_factor']}",
        "capture_inflated":   f"={r['capture_raw']}*{r['price_factor']}",
        # C
        "spot_revenue":       f"={r['net_prod']}*{r['capture_inf']}/1000",
        # D
        "ppa_fixed_payment":  f"={r['ppa_vol']}*{r['ppa_price']}/1000",
        "ppa_settlement":     f"={r['ppa_vol']}*{r['wholesale_inf']}/1000",
        "ppa_net_settlement": f"={r['ppa_fixed']}-{r['ppa_settle']}",
        # E
        "goo_volume":         f"=MAX(0,{r['net_prod']}-{r['total_ppa_vol']})",
        "goo_revenue":        f"={r['goo_vol']}*{r['goo_price']}/1000",
        # F
        "variable_tariff_cost": (
            f"={r['net_prod']}*{r['tariff_rate']}*{r['price_factor']}/1000"
        ),
        "ppa_management_cost":  f"={r['total_ppa_vol']}*{r['ppa_mgmt_rate']}/1000",
        # H
        "gross_revenue":        f"={r['spot']}+{r['ppa_rev']}+{r['goo_rev']}",
        "net_revenue":          f"={r['gross']}-{r['prod_costs']}-{r['balancing']}",
    }
