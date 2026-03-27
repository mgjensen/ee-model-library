"""
MODULE_ID:    OPEX_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "*"]
TECHNOLOGIES: ["PV"]
CREATED:      2026-03-17
MODIFIED:     2026-03-18

PV OPEX calculation — 11 sub-components per EE_MODEL_BUILD_SPEC.md §6.1.

Standard pattern per component:
    cost = annual_base × indexation_factor(year)
    accrued monthly = annual_cost / 12
    indexation_factor(y) = start_factor × (1 + inflation_rate)^max(0, y − inflation_start_year)

Volume-dependent components (self-consumption, VE-bonus) computed from
monthly production and price inputs.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §6.1
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# SUB-MODEL
# ============================================================================

class ComponentRate(BaseModel):
    """Parameters for a single fixed-rate OPEX component."""
    annual_DKKk: float = Field(0.0, description="Base annual cost DKKk (pre-inflation)")
    inflation_start_year: int = Field(2025, description="Year from which indexation compounds")
    indexation_start_factor: float = Field(1.0, description="Pre-indexation multiplier at period 0")


class ContractPeriod(BaseModel):
    """One O&M or land lease contract period with minimum cost floor."""
    cost_DKKk_per_unit_annual: float = Field(0.0, ge=0)
    duration_years: int = Field(1, gt=0)
    minimum_cost_DKKk_annual: float = Field(0.0, ge=0)


class OMConfig(BaseModel):
    """O&M with up to 7 contract periods, sequential."""
    periods: list[ContractPeriod] = Field(default_factory=list, max_length=7)
    unit_count: float = Field(0.0, ge=0, description="Units: MW, ha, etc.")


class LandLeaseConfig(BaseModel):
    """Land lease with up to 6 contract periods, sequential."""
    periods: list[ContractPeriod] = Field(default_factory=list, max_length=6)
    escalation_rate: float = Field(0.025)
    indexation_start_factor: float = Field(1.0)


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    inflation_rate: float = Field(0.025, description="Annual inflation rate for all components")
    sensitivity_factor: float = Field(1.0, description="Multiplier for scenario analysis")

    # Capacity
    capacity_mwp: float = Field(..., gt=0, description="Installed PV capacity MWp")

    # --- Fixed-rate components (annual DKKk total) ---
    # 1. O&M (quarterly payments → accrued monthly)
    om: ComponentRate

    # 2. Commercial management (monthly)
    commercial_management: ComponentRate

    # 3. Inverter replacement — annual_DKKk = rate_DKK_per_MW × capacity / 1000
    #    Active only after warranty expires
    inverter_replacement: ComponentRate
    inverter_warranty_end_period: int = Field(
        0, ge=0, description="0-based period after which inverter replacement is charged"
    )

    # 4. Land lease — rented area
    land_rented_ha: float = Field(0.0, ge=0)
    land_lease_rented: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="DKKk/ha/year → annual_DKKk = rate × land_rented_ha"
    )

    # 5. Land lease — owned area
    land_owned_ha: float = Field(0.0, ge=0)
    land_lease_owned: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="DKKk/ha/year → annual_DKKk = rate × land_owned_ha"
    )

    # 6. Lease tax (total area = rented + owned)
    lease_tax: ComponentRate = Field(
        default_factory=lambda: ComponentRate(annual_DKKk=0.0),
        description="annual_DKKk = rate_DKKk_per_ha × (rented + owned) ha"
    )

    # 7. Insurance (cyber + OAR + BI combined, monthly)
    insurance: ComponentRate

    # 8. Bank guarantee interest — simple: amount × rate / 12 per month
    guarantee_amount_DKKk: float = Field(0.0, ge=0, description="Guarantee face value DKKk")
    guarantee_rate: float = Field(0.0, ge=0, description="Annual guarantee commission rate")

    # 9. VE-bonus / community compensation — % of monthly spot revenue
    ve_bonus_pct: float = Field(0.0, ge=0, le=1.0, description="Fraction of spot revenue")
    spot_revenue: list[float] = Field(
        default_factory=list,
        description="Monthly spot revenue DKKk (length = periods if ve_bonus_pct > 0)"
    )

    # 10. Self-consumption (volume-dependent)
    self_consumption_mwh_per_mwp_annual: float = Field(
        0.0, ge=0, description="Annual self-consumption per MWp installed (MWh/MWp)"
    )
    wholesale_price: list[float] = Field(
        default_factory=list,
        description="Monthly wholesale electricity price DKK/MWh (length = periods if sc > 0)"
    )
    sc_inflation_start_year: int = Field(2025)
    sc_indexation_start_factor: float = Field(1.0)

    # 11. Other (auditor, spare parts) — monthly
    other: ComponentRate

    # Multi-period contract configs (override single-rate if populated)
    om_config: Optional[OMConfig] = Field(None, description="Multi-period O&M contract")
    land_lease_rented_config: Optional[LandLeaseConfig] = Field(None, description="Multi-period rented lease")
    land_lease_owned_config: Optional[LandLeaseConfig] = Field(None, description="Multi-period owned lease")

    @model_validator(mode="after")
    def _validate(self):
        if self.ve_bonus_pct > 0 and len(self.spot_revenue) != self.periods:
            raise ValueError(
                f"spot_revenue length {len(self.spot_revenue)} != periods {self.periods}"
            )
        if self.self_consumption_mwh_per_mwp_annual > 0 and len(self.wholesale_price) != self.periods:
            raise ValueError(
                f"wholesale_price length {len(self.wholesale_price)} != periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Monthly component arrays (length = periods) — accrual basis
    om: list[float]
    commercial_management: list[float]
    inverter_replacement: list[float]
    land_lease_rented: list[float]
    land_lease_owned: list[float]
    lease_tax: list[float]
    insurance: list[float]
    guarantee: list[float]
    ve_bonus: list[float]
    self_consumption: list[float]
    other: list[float]

    total_opex: list[float]       # sum across all components per period

    # Annual aggregation (one entry per calendar year spanned)
    annual_opex: list[float]

    # Lifetime total
    total_opex_lifetime: float


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


def _indexation_factor(
    year: int,
    inflation_start_year: int,
    indexation_start_factor: float,
    inflation_rate: float,
) -> float:
    years = max(0, year - inflation_start_year)
    return indexation_start_factor * (1.0 + inflation_rate) ** years


def _fixed_component(
    comp: ComponentRate,
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """
    Monthly accrual for a fixed-rate component.
    Returns annual_DKKk × indexation_factor / 12 for each period.
    """
    monthly = [0.0] * periods
    if comp.annual_DKKk == 0.0:
        return monthly
    for year, year_periods in year_groups.items():
        factor = _indexation_factor(
            year, comp.inflation_start_year, comp.indexation_start_factor, inflation_rate
        )
        monthly_amt = comp.annual_DKKk * factor / 12.0
        for p in year_periods:
            monthly[p] = monthly_amt
    return monthly


def _multi_period_component(
    config: OMConfig | LandLeaseConfig,
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
    start_year: int,
) -> list[float]:
    """Monthly cost from sequential contract periods with min cost floor."""
    monthly = [0.0] * periods
    if not config.periods:
        return monthly

    unit_count = config.unit_count if isinstance(config, OMConfig) else 1.0
    start_factor = config.indexation_start_factor if isinstance(config, LandLeaseConfig) else 1.0
    esc_rate = config.escalation_rate if isinstance(config, LandLeaseConfig) else inflation_rate

    # Build a flat list of (contract_period, year_offset) for each year
    contract_year = 0
    cp_idx = 0
    cp_years_used = 0
    for year, yp in year_groups.items():
        if cp_idx >= len(config.periods):
            break
        cp = config.periods[cp_idx]
        factor = start_factor * (1 + esc_rate) ** max(0, contract_year)
        rate_annual = cp.cost_DKKk_per_unit_annual * unit_count * factor
        min_annual = cp.minimum_cost_DKKk_annual * factor
        effective = max(rate_annual, min_annual) / 12.0
        for p in yp:
            monthly[p] = effective
        cp_years_used += 1
        contract_year += 1
        if cp_years_used >= cp.duration_years:
            cp_idx += 1
            cp_years_used = 0
    return monthly


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly PV OPEX calculation across all 11 sub-components."""
    yg = _year_groups(inputs.periods, inputs.start_year, inputs.start_month)
    ir = inputs.inflation_rate
    n = inputs.periods

    # 1. O&M
    if inputs.om_config and inputs.om_config.periods:
        om = _multi_period_component(inputs.om_config, yg, n, ir, inputs.start_year)
    else:
        om = _fixed_component(inputs.om, yg, n, ir)

    # 2. Commercial management
    commercial = _fixed_component(inputs.commercial_management, yg, n, ir)

    # 3. Inverter replacement (post-warranty only)
    inv_full = _fixed_component(inputs.inverter_replacement, yg, n, ir)
    inverter = [
        inv_full[p] if p >= inputs.inverter_warranty_end_period else 0.0
        for p in range(n)
    ]

    # 4. Land lease — rented
    if inputs.land_lease_rented_config and inputs.land_lease_rented_config.periods:
        ll_rented = _multi_period_component(inputs.land_lease_rented_config, yg, n, ir, inputs.start_year)
    else:
        ll_rented = _fixed_component(inputs.land_lease_rented, yg, n, ir)

    # 5. Land lease — owned
    if inputs.land_lease_owned_config and inputs.land_lease_owned_config.periods:
        ll_owned = _multi_period_component(inputs.land_lease_owned_config, yg, n, ir, inputs.start_year)
    else:
        ll_owned = _fixed_component(inputs.land_lease_owned, yg, n, ir)

    # 6. Lease tax
    lt = _fixed_component(inputs.lease_tax, yg, n, ir)

    # 7. Insurance
    insurance = _fixed_component(inputs.insurance, yg, n, ir)

    # 8. Bank guarantee interest (flat monthly, no inflation)
    guarantee_monthly = inputs.guarantee_amount_DKKk * inputs.guarantee_rate / 12.0
    guarantee = [guarantee_monthly] * n

    # 9. VE-bonus
    if inputs.ve_bonus_pct > 0:
        ve = [inputs.ve_bonus_pct * inputs.spot_revenue[p] for p in range(n)]
    else:
        ve = [0.0] * n

    # 10. Self-consumption
    if inputs.self_consumption_mwh_per_mwp_annual > 0:
        monthly_energy_mwh = inputs.capacity_mwp * inputs.self_consumption_mwh_per_mwp_annual / 12.0
        sc: list[float] = []
        for p in range(n):
            year = inputs.start_year + (inputs.start_month - 1 + p) // 12
            factor = _indexation_factor(
                year, inputs.sc_inflation_start_year,
                inputs.sc_indexation_start_factor, ir
            )
            # wholesale_price in DKK/MWh → cost in DKKk = MWh × DKK/MWh / 1000
            sc.append(monthly_energy_mwh * inputs.wholesale_price[p] * factor / 1000.0)
    else:
        sc = [0.0] * n

    # 11. Other
    other = _fixed_component(inputs.other, yg, n, ir)

    # Total OPEX per period
    total = [
        om[p] + commercial[p] + inverter[p] + ll_rented[p] + ll_owned[p]
        + lt[p] + insurance[p] + guarantee[p] + ve[p] + sc[p] + other[p]
        for p in range(n)
    ]

    # Apply sensitivity factor
    if inputs.sensitivity_factor != 1.0:
        sf = inputs.sensitivity_factor
        total = [v * sf for v in total]

    # Annual aggregation
    annual = [sum(total[p] for p in plist) for plist in yg.values()]

    return Outputs(
        om=om,
        commercial_management=commercial,
        inverter_replacement=inverter,
        land_lease_rented=ll_rented,
        land_lease_owned=ll_owned,
        lease_tax=lt,
        insurance=insurance,
        guarantee=guarantee,
        ve_bonus=ve,
        self_consumption=sc,
        other=other,
        total_opex=total,
        annual_opex=annual,
        total_opex_lifetime=sum(total),
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key OPEX rows.
    refs keys: annual_base, indexation_factor, inv_base, warranty_flag,
               capacity_mwp, sc_mwh_per_mwp, wholesale_price,
               ve_pct, spot_revenue, guarantee_amount, guarantee_rate
    """
    r = refs
    return {
        "indexation_factor": (
            f"={r['start_factor']}*(1+{r['inflation_rate']})^MAX(0,{r['year']}-{r['inf_start_year']})"
        ),
        "fixed_component_monthly": (
            f"={r['annual_base']}*{r['indexation_factor']}/12"
        ),
        "inverter_replacement": (
            f"=IF({r['period']}>={r['warranty_end']},{r['inv_base']}*{r['indexation_factor']}/12,0)"
        ),
        "self_consumption": (
            f"={r['capacity_mwp']}*{r['sc_mwh_per_mwp']}/12*{r['wholesale_price']}*{r['indexation_factor']}/1000"
        ),
        "ve_bonus": (
            f"={r['ve_pct']}*{r['spot_revenue']}"
        ),
        "guarantee_interest": (
            f"={r['guarantee_amount']}*{r['guarantee_rate']}/12"
        ),
        "total_opex": (
            f"=SUM({r['om']}:{r['other']})"
        ),
    }
