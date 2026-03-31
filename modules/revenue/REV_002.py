"""
MODULE_ID:    REV_002
VERSION:      1.1
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "*"]
TECHNOLOGIES: ["BESS"]
CREATED:      2026-03-17
MODIFIED:     2026-03-29

BESS revenue calculation — sub-sections A through G per EE_MODEL_BUILD_SPEC.md §5.2.
Every line item is a named output field; no opaque totals.

Sub-sections:
  A  Discharge revenue    — volume × inflated price
  B  Charging costs       — grid charging + PV charging (both inflated)
  C  Import production costs (grid → BESS):
       feed-in tariff, net loss tariff, tiered system tariff, fixed import fee
  D  Export production costs (BESS → grid):
       TSO, DSO, Nord Pool tariffs on discharge volume
  E  System adjustments   — GoO revenue lost to PV round-trip losses,
                            export tariff add-back on lost PV volume
  F  Multimarket revenue  — % of BESS net revenue (ex-multimarket) from curve
  G  Summary              — gross revenue, total costs, net revenue
  H  Tolling agreement    — contracted MW × annual fee
  I  FCAS revenue         — regulation + contingency ancillary services (AU NEM)
  J  VTA compensation     — 4-layer deductions from virtual tolling agreement

Source: EE_MODEL_BUILD_SPEC.md v2.0 §5.2
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# COMPONENT SUB-MODELS
# ============================================================================

class VariableTariff(BaseModel):
    """Variable cost / revenue tariff with annual indexation."""
    rate_DKK_per_MWh: float = Field(0.0, ge=0)
    inflation_start_year: int = Field(2025)
    indexation_start_factor: float = Field(1.0, gt=0)


class SystemTariff(BaseModel):
    """
    Tiered system tariff on grid import volume.
    Annual import ≤ tier1_threshold → tier1_rate; remainder → tier2_rate.
    Threshold and rates both indexed annually.
    """
    tier1_threshold_MWh_annual: float = Field(0.0, ge=0)
    tier1_rate_DKK_per_MWh: float = Field(0.0, ge=0)
    tier2_rate_DKK_per_MWh: float = Field(0.0, ge=0)
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
    inflation_rate: float = Field(0.025)

    # BESS capacity
    power_MW: float = Field(..., gt=0, description="BESS power rating MW")
    energy_MWh_installed: float = Field(..., gt=0, description="BESS energy rating MWh")
    round_trip_efficiency: float = Field(..., gt=0, le=1.0, description="RTE 0–1")

    # --- A. Discharge revenue ---
    discharge_volume_MWh: list[float]         # monthly MWh discharged
    discharge_price_DKK_per_MWh: list[float]  # monthly DKK/MWh
    discharge_inflation_start_year: int = Field(2025)
    discharge_indexation_start_factor: float = Field(1.0, gt=0)

    # --- B. Charging costs ---
    grid_charge_volume_MWh: list[float]        # monthly MWh charged from grid
    grid_charge_price_DKK_per_MWh: list[float] # monthly DKK/MWh
    pv_charge_volume_MWh: list[float]          # monthly MWh charged from co-located PV
    pv_charge_price_DKK_per_MWh: list[float]   # monthly DKK/MWh (PV capture price)
    charge_inflation_start_year: int = Field(2025)
    charge_indexation_start_factor: float = Field(1.0, gt=0)

    # --- C. Import production costs ---
    feed_in_tariff: VariableTariff = Field(default_factory=VariableTariff)
    net_loss_pct: float = Field(0.0, ge=0, description="Net loss % of (charge_price + markup)")
    net_loss_markup_DKK_per_MWh: float = Field(0.0, ge=0)
    system_tariff: SystemTariff = Field(default_factory=SystemTariff)
    grid_import_fee_DKK_per_MW_annual: float = Field(
        0.0, ge=0, description="Fixed annual grid import fee per MW capacity"
    )

    # --- D. Export production costs ---
    tso_export_tariff: VariableTariff = Field(default_factory=VariableTariff)
    dso_export_tariff: VariableTariff = Field(default_factory=VariableTariff)
    nordpool_export_tariff: VariableTariff = Field(default_factory=VariableTariff)

    # --- E. System adjustments ---
    goo_price_DKK_per_MWh: list[float]  # monthly GoO price (pre-inflated if desired)

    # --- F. Multimarket ---
    multimarket_pct: list[float]  # monthly fraction [0..1] of base net revenue

    # --- H. Tolling agreement (optional) ---
    tolling_active: bool = Field(False, description="Enable tolling agreement slot")
    tolling_contracted_mw: float = Field(0.0, ge=0, description="Contracted MW for tolling")
    tolling_price_DKK_per_mw_per_year: float = Field(
        0.0, ge=0, description="Annual tolling fee DKK/MW/year"
    )
    tolling_start_period: int = Field(0, ge=0, description="0-based first period of tolling")
    tolling_end_period: int = Field(0, ge=0, description="0-based last period of tolling (inclusive)")
    tolling_inflation_rate: float = Field(0.025, ge=0, description="Annual inflation for tolling fee")
    tolling_inflation_start_year: int = Field(2025, description="Year from which tolling inflation compounds")

    # --- Optional degradation curves (annual values, override flat RTE/capacity) ---
    rte_curve: Optional[list[float]] = Field(
        None, description="Annual RTE values (0-1), one per operational year. Overrides flat round_trip_efficiency."
    )
    capacity_curve: Optional[list[float]] = Field(
        None, description="Annual capacity retention factors (0-1), one per operational year. Scales discharge volume."
    )

    # --- I. FCAS revenue (AU NEM ancillary services) ---
    fcas_active: bool = Field(False, description="Enable FCAS revenue streams")
    fcas_regulation_price_per_MWh: list[float] = Field(default_factory=list)
    fcas_contingency_price_per_MWh: list[float] = Field(default_factory=list)

    # --- J. VTA compensation (virtual tolling agreement risk layers) ---
    vta_active: bool = Field(False, description="Enable VTA compensation deductions from tolling")
    vta_contracted_discharge_MWh: list[float] = Field(default_factory=list)
    vta_discharge_price_per_MWh: list[float] = Field(default_factory=list)
    vta_colocation_active: bool = Field(False)
    vta_colocation_shortfall_pct: list[float] = Field(default_factory=list)
    vta_capacity_shortfall_active: bool = Field(False)
    vta_capacity_shortfall_pct: list[float] = Field(default_factory=list)
    vta_degradation_active: bool = Field(False)
    vta_degradation_shortfall_pct: list[float] = Field(default_factory=list)
    vta_transmission_active: bool = Field(False)
    vta_transmission_loss_pct: list[float] = Field(default_factory=list)

    # External curve pass-through mode
    external_mode: bool = Field(False, description="If True, use external BESS revenue curve instead of internal calc")
    external_bess_revenue_DKKk: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if self.external_mode:
            if self.external_bess_revenue_DKKk and len(self.external_bess_revenue_DKKk) != n:
                raise ValueError(
                    f"external_bess_revenue_DKKk length "
                    f"{len(self.external_bess_revenue_DKKk)} != periods {n}"
                )
            # Still validate tolling if active
            if self.tolling_active:
                if self.tolling_end_period >= n:
                    raise ValueError(
                        f"tolling_end_period {self.tolling_end_period} >= periods {n}"
                    )
                if self.tolling_start_period > self.tolling_end_period:
                    raise ValueError(
                        f"tolling_start_period {self.tolling_start_period} > "
                        f"tolling_end_period {self.tolling_end_period}"
                    )
            # VTA works in external mode (has own volume/price inputs)
            self._validate_vta(n)
            # FCAS skipped in external mode (no discharge volume)
            return self
        time_series = {
            "discharge_volume_MWh": self.discharge_volume_MWh,
            "discharge_price_DKK_per_MWh": self.discharge_price_DKK_per_MWh,
            "grid_charge_volume_MWh": self.grid_charge_volume_MWh,
            "grid_charge_price_DKK_per_MWh": self.grid_charge_price_DKK_per_MWh,
            "pv_charge_volume_MWh": self.pv_charge_volume_MWh,
            "pv_charge_price_DKK_per_MWh": self.pv_charge_price_DKK_per_MWh,
            "goo_price_DKK_per_MWh": self.goo_price_DKK_per_MWh,
            "multimarket_pct": self.multimarket_pct,
        }
        for name, arr in time_series.items():
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        if self.tolling_active:
            if self.tolling_end_period >= n:
                raise ValueError(
                    f"tolling_end_period {self.tolling_end_period} >= periods {n}"
                )
            if self.tolling_start_period > self.tolling_end_period:
                raise ValueError(
                    f"tolling_start_period {self.tolling_start_period} > "
                    f"tolling_end_period {self.tolling_end_period}"
                )
        if self.rte_curve is not None:
            if not self.rte_curve:
                raise ValueError("rte_curve must not be empty when provided")
            if any(v <= 0 or v > 1.0 for v in self.rte_curve):
                raise ValueError("rte_curve values must be in (0, 1]")
        if self.capacity_curve is not None:
            if not self.capacity_curve:
                raise ValueError("capacity_curve must not be empty when provided")
            if any(v <= 0 or v > 1.0 for v in self.capacity_curve):
                raise ValueError("capacity_curve values must be in (0, 1]")
        # FCAS validation
        if self.fcas_active:
            for name, arr in [
                ("fcas_regulation_price_per_MWh", self.fcas_regulation_price_per_MWh),
                ("fcas_contingency_price_per_MWh", self.fcas_contingency_price_per_MWh),
            ]:
                if arr and len(arr) != n:
                    raise ValueError(f"{name} length {len(arr)} != periods {n}")
        # VTA validation
        self._validate_vta(n)
        return self

    def _validate_vta(self, n: int) -> None:
        if not self.vta_active:
            return
        for name, arr in [
            ("vta_contracted_discharge_MWh", self.vta_contracted_discharge_MWh),
            ("vta_discharge_price_per_MWh", self.vta_discharge_price_per_MWh),
        ]:
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        layer_checks = [
            (self.vta_colocation_active, "vta_colocation_shortfall_pct", self.vta_colocation_shortfall_pct),
            (self.vta_capacity_shortfall_active, "vta_capacity_shortfall_pct", self.vta_capacity_shortfall_pct),
            (self.vta_degradation_active, "vta_degradation_shortfall_pct", self.vta_degradation_shortfall_pct),
            (self.vta_transmission_active, "vta_transmission_loss_pct", self.vta_transmission_loss_pct),
        ]
        for active, name, arr in layer_checks:
            if active and arr and len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")


# ============================================================================
# OUTPUTS — every sub-section, every line item
# ============================================================================

class Outputs(BaseModel):
    # --- A. Discharge revenue ---
    discharge_volume: list[float]               # MWh — pass-through
    discharge_indexation_factor: list[float]    # dimensionless
    discharge_price_inflated: list[float]       # DKK/MWh
    discharge_revenue: list[float]              # DKKk

    # --- B. Charging costs ---
    charge_indexation_factor: list[float]       # dimensionless
    grid_charge_price_inflated: list[float]     # DKK/MWh
    pv_charge_price_inflated: list[float]       # DKK/MWh
    grid_charging_cost: list[float]             # DKKk
    pv_charging_cost: list[float]               # DKKk
    total_charging_cost: list[float]            # DKKk

    # --- C. Import production costs (grid → BESS) ---
    feed_in_tariff_cost: list[float]            # DKKk
    net_loss_tariff_cost: list[float]           # DKKk
    system_tariff_cost: list[float]             # DKKk (tiered)
    grid_import_fee: list[float]                # DKKk (fixed, monthly spread)
    total_import_production_costs: list[float]  # DKKk

    # --- D. Export production costs (BESS → grid) ---
    tso_export_cost: list[float]                # DKKk
    dso_export_cost: list[float]                # DKKk
    nordpool_export_cost: list[float]           # DKKk
    total_export_production_costs: list[float]  # DKKk

    # --- E. System adjustments ---
    goo_lost_volume: list[float]                # MWh — PV round-trip losses
    goo_revenue_lost: list[float]               # DKKk — negative (cost)
    export_tariff_addback: list[float]          # DKKk — positive (benefit)
    total_system_adjustments: list[float]       # DKKk — net (addback − goo_lost)

    # --- F. Multimarket ---
    bess_net_ex_multimarket: list[float]        # DKKk — base for multimarket %
    multimarket_revenue: list[float]            # DKKk

    # --- H. Tolling ---
    tolling_revenue: list[float]               # DKKk — contracted MW × price/MW/yr / 12

    # --- I. FCAS ---
    fcas_regulation_revenue: list[float]        # currency-k
    fcas_contingency_revenue: list[float]       # currency-k
    fcas_total_revenue: list[float]             # currency-k

    # --- J. VTA compensation ---
    vta_colocation_compensation: list[float]    # currency-k (negative)
    vta_capacity_compensation: list[float]      # currency-k (negative)
    vta_degradation_compensation: list[float]   # currency-k (negative)
    vta_transmission_compensation: list[float]  # currency-k (negative)
    vta_total_compensation: list[float]         # currency-k (sum of 4 layers)

    # --- G. Summary ---
    gross_revenue: list[float]                  # DKKk — discharge + multimarket + tolling + FCAS
    total_costs: list[float]                    # DKKk — charging + import + export − adjustments
    net_revenue: list[float]                    # DKKk — gross − costs + VTA compensation

    # --- Degradation curves (actual values used per period) ---
    effective_rte: list[float]                 # RTE applied each period (flat or from curve)
    effective_capacity_factor: list[float]     # capacity factor each period (1.0 or from curve)

    # Annual aggregation
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


def _indexation_factor(
    year: int,
    inflation_start_year: int,
    indexation_start_factor: float,
    inflation_rate: float,
) -> float:
    years = max(0, year - inflation_start_year)
    return indexation_start_factor * (1.0 + inflation_rate) ** years


def _factor_series(
    year_groups: OrderedDict,
    periods: int,
    inflation_start_year: int,
    indexation_start_factor: float,
    inflation_rate: float,
) -> list[float]:
    out = [0.0] * periods
    for year, year_periods in year_groups.items():
        f = _indexation_factor(year, inflation_start_year, indexation_start_factor, inflation_rate)
        for p in year_periods:
            out[p] = f
    return out


def _annual_to_monthly(
    curve: list[float],
    year_groups: OrderedDict,
    periods: int,
    default: float,
) -> list[float]:
    """Map annual curve values to monthly periods.

    Year 0 in the curve maps to the first calendar year in year_groups.
    If the curve is shorter than the number of years, the last value is held.
    """
    out = [default] * periods
    years = list(year_groups.keys())
    for i, year in enumerate(years):
        if i < len(curve):
            val = curve[i]
        else:
            val = curve[-1]  # hold last value
        for p in year_groups[year]:
            out[p] = val
    return out


def _variable_tariff_cost(
    tariff: VariableTariff,
    volume: list[float],
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """Monthly cost = volume[p] × tariff_rate × indexation_factor / 1000 (DKKk)."""
    if tariff.rate_DKK_per_MWh == 0.0:
        return [0.0] * periods
    out = [0.0] * periods
    for year, year_periods in year_groups.items():
        f = _indexation_factor(
            year, tariff.inflation_start_year, tariff.indexation_start_factor, inflation_rate
        )
        for p in year_periods:
            out[p] = volume[p] * tariff.rate_DKK_per_MWh * f / 1000.0
    return out


def _system_tariff_series(
    grid_charge_vol: list[float],
    tariff: SystemTariff,
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """
    Tiered system tariff on annual cumulative import volume.
    Resets each calendar year. Computed period-by-period, earliest periods first.
    """
    out = [0.0] * periods
    if tariff.tier1_rate_DKK_per_MWh == 0.0 and tariff.tier2_rate_DKK_per_MWh == 0.0:
        return out
    for year, year_periods in year_groups.items():
        f = _indexation_factor(
            year, tariff.inflation_start_year, tariff.indexation_start_factor, inflation_rate
        )
        cumulative = 0.0
        for p in year_periods:
            vol = grid_charge_vol[p]
            remaining_tier1 = max(0.0, tariff.tier1_threshold_MWh_annual - cumulative)
            tier1_vol = min(vol, remaining_tier1)
            tier2_vol = max(0.0, vol - tier1_vol)
            out[p] = (
                tier1_vol * tariff.tier1_rate_DKK_per_MWh
                + tier2_vol * tariff.tier2_rate_DKK_per_MWh
            ) * f / 1000.0
            cumulative += vol
    return out


def _combined_export_tariff_rate_inflated(
    tso: VariableTariff,
    dso: VariableTariff,
    nordpool: VariableTariff,
    year_groups: OrderedDict,
    periods: int,
    inflation_rate: float,
) -> list[float]:
    """Combined inflated export tariff rate per period (DKK/MWh)."""
    out = [0.0] * periods
    for year, year_periods in year_groups.items():
        tso_f = _indexation_factor(year, tso.inflation_start_year, tso.indexation_start_factor, inflation_rate)
        dso_f = _indexation_factor(year, dso.inflation_start_year, dso.indexation_start_factor, inflation_rate)
        np_f  = _indexation_factor(year, nordpool.inflation_start_year, nordpool.indexation_start_factor, inflation_rate)
        combined = (
            tso.rate_DKK_per_MWh * tso_f
            + dso.rate_DKK_per_MWh * dso_f
            + nordpool.rate_DKK_per_MWh * np_f
        )
        for p in year_periods:
            out[p] = combined
    return out


# ============================================================================
# VTA + FCAS HELPERS
# ============================================================================

def _vta_layer(active: bool, pct: list[float], vol: list[float],
               price: list[float], n: int) -> list[float]:
    if not active:
        return [0.0] * n
    pct_arr = pct if pct else [0.0] * n
    return [-pct_arr[p] * vol[p] * price[p] / 1000.0 for p in range(n)]


def _compute_vta(inputs, n: int):
    zeros = [0.0] * n
    if not inputs.vta_active:
        return zeros[:], zeros[:], zeros[:], zeros[:], zeros[:]
    vol = inputs.vta_contracted_discharge_MWh or zeros
    price = inputs.vta_discharge_price_per_MWh or zeros
    coloc = _vta_layer(inputs.vta_colocation_active, inputs.vta_colocation_shortfall_pct, vol, price, n)
    cap = _vta_layer(inputs.vta_capacity_shortfall_active, inputs.vta_capacity_shortfall_pct, vol, price, n)
    deg = _vta_layer(inputs.vta_degradation_active, inputs.vta_degradation_shortfall_pct, vol, price, n)
    tx = _vta_layer(inputs.vta_transmission_active, inputs.vta_transmission_loss_pct, vol, price, n)
    total = [coloc[p] + cap[p] + deg[p] + tx[p] for p in range(n)]
    return coloc, cap, deg, tx, total


def _compute_fcas(inputs, dis_volume: list[float], n: int):
    zeros = [0.0] * n
    if not inputs.fcas_active:
        return zeros[:], zeros[:], zeros[:]
    reg_price = inputs.fcas_regulation_price_per_MWh or zeros
    con_price = inputs.fcas_contingency_price_per_MWh or zeros
    reg = [dis_volume[p] * reg_price[p] / 1000.0 for p in range(n)]
    con = [dis_volume[p] * con_price[p] / 1000.0 for p in range(n)]
    total = [reg[p] + con[p] for p in range(n)]
    return reg, con, total


# ============================================================================
# MAIN CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Full BESS revenue calculation across sub-sections A–G."""
    n = inputs.periods
    yg = _year_groups(n, inputs.start_year, inputs.start_month)
    ir = inputs.inflation_rate

    if inputs.external_mode:
        ext_rev = inputs.external_bess_revenue_DKKk or [0.0] * n
        zeros = [0.0] * n

        # Compute tolling on top of external revenue if active
        tolling_rev = [0.0] * n
        if inputs.tolling_active and inputs.tolling_contracted_mw > 0:
            annual_fee = inputs.tolling_contracted_mw * inputs.tolling_price_DKK_per_mw_per_year
            for year, year_periods in yg.items():
                factor = _indexation_factor(
                    year, inputs.tolling_inflation_start_year, 1.0, inputs.tolling_inflation_rate
                )
                monthly_fee = annual_fee * factor / 12.0 / 1000.0
                for p in year_periods:
                    if inputs.tolling_start_period <= p <= inputs.tolling_end_period:
                        tolling_rev[p] = monthly_fee

        # VTA compensation works in external mode (has own volume/price inputs)
        vta_coloc, vta_cap, vta_deg, vta_tx, vta_total = _compute_vta(inputs, n)

        gross = [ext_rev[p] + tolling_rev[p] for p in range(n)]
        net_rev = [gross[p] + vta_total[p] for p in range(n)]
        annual_net = [sum(net_rev[p] for p in plist) for plist in yg.values()]
        return Outputs(
            discharge_volume=zeros[:],
            discharge_indexation_factor=[1.0] * n,
            discharge_price_inflated=zeros[:],
            discharge_revenue=zeros[:],
            charge_indexation_factor=[1.0] * n,
            grid_charge_price_inflated=zeros[:],
            pv_charge_price_inflated=zeros[:],
            grid_charging_cost=zeros[:],
            pv_charging_cost=zeros[:],
            total_charging_cost=zeros[:],
            feed_in_tariff_cost=zeros[:],
            net_loss_tariff_cost=zeros[:],
            system_tariff_cost=zeros[:],
            grid_import_fee=zeros[:],
            total_import_production_costs=zeros[:],
            tso_export_cost=zeros[:],
            dso_export_cost=zeros[:],
            nordpool_export_cost=zeros[:],
            total_export_production_costs=zeros[:],
            goo_lost_volume=zeros[:],
            goo_revenue_lost=zeros[:],
            export_tariff_addback=zeros[:],
            total_system_adjustments=zeros[:],
            bess_net_ex_multimarket=ext_rev,
            multimarket_revenue=zeros[:],
            tolling_revenue=tolling_rev,
            fcas_regulation_revenue=zeros[:],
            fcas_contingency_revenue=zeros[:],
            fcas_total_revenue=zeros[:],
            vta_colocation_compensation=vta_coloc,
            vta_capacity_compensation=vta_cap,
            vta_degradation_compensation=vta_deg,
            vta_transmission_compensation=vta_tx,
            vta_total_compensation=vta_total,
            effective_rte=[inputs.round_trip_efficiency] * n,
            effective_capacity_factor=[1.0] * n,
            gross_revenue=gross,
            total_costs=zeros[:],
            net_revenue=net_rev,
            annual_net_revenue=annual_net,
            total_net_revenue=sum(net_rev),
        )

    # --- Degradation curves ---
    if inputs.capacity_curve is not None:
        cap_factors = _annual_to_monthly(inputs.capacity_curve, yg, n, 1.0)
    else:
        cap_factors = [1.0] * n
    if inputs.rte_curve is not None:
        rte_monthly = _annual_to_monthly(inputs.rte_curve, yg, n, inputs.round_trip_efficiency)
    else:
        rte_monthly = [inputs.round_trip_efficiency] * n

    # --- A. Discharge revenue ---
    dis_factor = _factor_series(
        yg, n, inputs.discharge_inflation_start_year,
        inputs.discharge_indexation_start_factor, ir
    )
    dis_price_inf = [
        inputs.discharge_price_DKK_per_MWh[p] * dis_factor[p] for p in range(n)
    ]
    # Capacity curve scales discharge volume (effective energy available)
    dis_volume = [inputs.discharge_volume_MWh[p] * cap_factors[p] for p in range(n)]
    dis_revenue = [dis_volume[p] * dis_price_inf[p] / 1000.0 for p in range(n)]

    # --- B. Charging costs ---
    chg_factor = _factor_series(
        yg, n, inputs.charge_inflation_start_year,
        inputs.charge_indexation_start_factor, ir
    )
    grid_price_inf = [
        inputs.grid_charge_price_DKK_per_MWh[p] * chg_factor[p] for p in range(n)
    ]
    pv_price_inf = [
        inputs.pv_charge_price_DKK_per_MWh[p] * chg_factor[p] for p in range(n)
    ]
    grid_chg_cost = [
        inputs.grid_charge_volume_MWh[p] * grid_price_inf[p] / 1000.0 for p in range(n)
    ]
    pv_chg_cost = [
        inputs.pv_charge_volume_MWh[p] * pv_price_inf[p] / 1000.0 for p in range(n)
    ]
    total_chg_cost = [grid_chg_cost[p] + pv_chg_cost[p] for p in range(n)]

    # --- C. Import production costs ---
    # C1: Feed-in tariff
    feed_in_cost = _variable_tariff_cost(
        inputs.feed_in_tariff, inputs.grid_charge_volume_MWh, yg, n, ir
    )

    # C2: Net loss tariff = pct × (grid_charge_price_inflated + markup) × volume / 1000
    net_loss_cost = [
        inputs.net_loss_pct
        * (grid_price_inf[p] + inputs.net_loss_markup_DKK_per_MWh)
        * inputs.grid_charge_volume_MWh[p]
        / 1000.0
        for p in range(n)
    ]

    # C3: System tariff (tiered annual cumulative)
    sys_tariff_cost = _system_tariff_series(
        inputs.grid_charge_volume_MWh, inputs.system_tariff, yg, n, ir
    )

    # C4: Fixed grid import fee (per MW, annual → spread monthly)
    annual_import_fee = inputs.power_MW * inputs.grid_import_fee_DKK_per_MW_annual
    # Spread evenly across months in each year (accounts for partial first/last year)
    import_fee = [0.0] * n
    for year_periods in yg.values():
        monthly_fee = annual_import_fee / len(year_periods) / 1000.0  # DKKk
        for p in year_periods:
            import_fee[p] = monthly_fee

    total_import_costs = [
        feed_in_cost[p] + net_loss_cost[p] + sys_tariff_cost[p] + import_fee[p]
        for p in range(n)
    ]

    # --- D. Export production costs ---
    tso_exp_cost = _variable_tariff_cost(
        inputs.tso_export_tariff, dis_volume, yg, n, ir
    )
    dso_exp_cost = _variable_tariff_cost(
        inputs.dso_export_tariff, dis_volume, yg, n, ir
    )
    np_exp_cost = _variable_tariff_cost(
        inputs.nordpool_export_tariff, dis_volume, yg, n, ir
    )
    total_export_costs = [
        tso_exp_cost[p] + dso_exp_cost[p] + np_exp_cost[p] for p in range(n)
    ]

    # --- E. System adjustments ---
    # Round-trip losses on PV-supplied energy (uses per-period RTE from curve or flat)
    goo_lost_vol = [
        inputs.pv_charge_volume_MWh[p] * (1.0 - rte_monthly[p]) for p in range(n)
    ]
    # GoO revenue lost (negative for P&L; cost to the project)
    goo_rev_lost = [
        -goo_lost_vol[p] * inputs.goo_price_DKK_per_MWh[p] / 1000.0 for p in range(n)
    ]
    # Export tariff add-back for PV losses (these MWh were never actually exported)
    combined_exp_rate = _combined_export_tariff_rate_inflated(
        inputs.tso_export_tariff, inputs.dso_export_tariff,
        inputs.nordpool_export_tariff, yg, n, ir
    )
    exp_addback = [
        goo_lost_vol[p] * combined_exp_rate[p] / 1000.0 for p in range(n)
    ]
    # Net system adjustment = addback − goo_lost (addback positive, goo_lost negative)
    total_sys_adj = [exp_addback[p] + goo_rev_lost[p] for p in range(n)]

    # --- F. Multimarket ---
    # Base = discharge_revenue − total_charging_cost − total_import_costs
    #        − total_export_costs + total_system_adjustments
    bess_net_ex_multi = [
        dis_revenue[p]
        - total_chg_cost[p]
        - total_import_costs[p]
        - total_export_costs[p]
        + total_sys_adj[p]
        for p in range(n)
    ]
    multi_rev = [
        inputs.multimarket_pct[p] * bess_net_ex_multi[p] for p in range(n)
    ]

    # --- H. Tolling agreement ---
    tolling_rev = [0.0] * n
    if inputs.tolling_active and inputs.tolling_contracted_mw > 0:
        annual_fee = inputs.tolling_contracted_mw * inputs.tolling_price_DKK_per_mw_per_year
        for year, year_periods in yg.items():
            factor = _indexation_factor(
                year, inputs.tolling_inflation_start_year, 1.0, inputs.tolling_inflation_rate
            )
            monthly_fee = annual_fee * factor / 12.0 / 1000.0  # DKKk
            for p in year_periods:
                if inputs.tolling_start_period <= p <= inputs.tolling_end_period:
                    tolling_rev[p] = monthly_fee

    # --- I. FCAS revenue ---
    fcas_reg, fcas_con, fcas_total = _compute_fcas(inputs, dis_volume, n)

    # --- J. VTA compensation ---
    vta_coloc, vta_cap, vta_deg, vta_tx, vta_total = _compute_vta(inputs, n)

    # --- G. Summary ---
    gross_rev = [dis_revenue[p] + multi_rev[p] + tolling_rev[p] + fcas_total[p]
                 for p in range(n)]
    total_costs = [
        total_chg_cost[p]
        + total_import_costs[p]
        + total_export_costs[p]
        - total_sys_adj[p]  # adjustments reduce net costs
        for p in range(n)
    ]
    net_rev = [gross_rev[p] - total_costs[p] + vta_total[p] for p in range(n)]

    annual_net = [sum(net_rev[p] for p in plist) for plist in yg.values()]

    return Outputs(
        # A
        discharge_volume=dis_volume,
        discharge_indexation_factor=dis_factor,
        discharge_price_inflated=dis_price_inf,
        discharge_revenue=dis_revenue,
        # B
        charge_indexation_factor=chg_factor,
        grid_charge_price_inflated=grid_price_inf,
        pv_charge_price_inflated=pv_price_inf,
        grid_charging_cost=grid_chg_cost,
        pv_charging_cost=pv_chg_cost,
        total_charging_cost=total_chg_cost,
        # C
        feed_in_tariff_cost=feed_in_cost,
        net_loss_tariff_cost=net_loss_cost,
        system_tariff_cost=sys_tariff_cost,
        grid_import_fee=import_fee,
        total_import_production_costs=total_import_costs,
        # D
        tso_export_cost=tso_exp_cost,
        dso_export_cost=dso_exp_cost,
        nordpool_export_cost=np_exp_cost,
        total_export_production_costs=total_export_costs,
        # E
        goo_lost_volume=goo_lost_vol,
        goo_revenue_lost=goo_rev_lost,
        export_tariff_addback=exp_addback,
        total_system_adjustments=total_sys_adj,
        # F
        bess_net_ex_multimarket=bess_net_ex_multi,
        multimarket_revenue=multi_rev,
        # H
        tolling_revenue=tolling_rev,
        # I
        fcas_regulation_revenue=fcas_reg,
        fcas_contingency_revenue=fcas_con,
        fcas_total_revenue=fcas_total,
        # J
        vta_colocation_compensation=vta_coloc,
        vta_capacity_compensation=vta_cap,
        vta_degradation_compensation=vta_deg,
        vta_transmission_compensation=vta_tx,
        vta_total_compensation=vta_total,
        # Degradation
        effective_rte=rte_monthly,
        effective_capacity_factor=cap_factors,
        # G
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
        # A
        "discharge_indexation_factor": (
            f"={r['dis_start_factor']}*(1+{r['inf_rate']})^MAX(0,{r['year']}-{r['dis_inf_start']})"
        ),
        "discharge_price_inflated":  f"={r['dis_price_raw']}*{r['dis_factor']}",
        "discharge_revenue":         f"={r['dis_vol']}*{r['dis_price_inf']}/1000",
        # B
        "charge_indexation_factor": (
            f"={r['chg_start_factor']}*(1+{r['inf_rate']})^MAX(0,{r['year']}-{r['chg_inf_start']})"
        ),
        "grid_charge_price_inflated": f"={r['grid_price_raw']}*{r['chg_factor']}",
        "pv_charge_price_inflated":   f"={r['pv_price_raw']}*{r['chg_factor']}",
        "grid_charging_cost":         f"={r['grid_vol']}*{r['grid_price_inf']}/1000",
        "pv_charging_cost":           f"={r['pv_vol']}*{r['pv_price_inf']}/1000",
        # C
        "feed_in_tariff_cost":        f"={r['grid_vol']}*{r['feed_in_rate']}*{r['chg_factor']}/1000",
        "net_loss_tariff_cost": (
            f"={r['net_loss_pct']}*({r['grid_price_inf']}+{r['markup']})*{r['grid_vol']}/1000"
        ),
        "grid_import_fee":            f"={r['power_mw']}*{r['import_fee_rate']}/12/1000",
        # D
        "tso_export_cost":            f"={r['dis_vol']}*{r['tso_rate']}*{r['tso_factor']}/1000",
        "dso_export_cost":            f"={r['dis_vol']}*{r['dso_rate']}*{r['dso_factor']}/1000",
        "nordpool_export_cost":       f"={r['dis_vol']}*{r['np_rate']}*{r['np_factor']}/1000",
        # E
        "goo_lost_volume":            f"={r['pv_vol']}*(1-{r['rte']})",
        "goo_revenue_lost":           f"=-{r['goo_lost_vol']}*{r['goo_price']}/1000",
        "export_tariff_addback":      f"={r['goo_lost_vol']}*({r['tso_rate_inf']}+{r['dso_rate_inf']}+{r['np_rate_inf']})/1000",
        # F
        "bess_net_ex_multimarket": (
            f"={r['dis_rev']}-{r['chg_cost']}-{r['import_costs']}-{r['export_costs']}+{r['sys_adj']}"
        ),
        "multimarket_revenue":        f"={r['multi_pct']}*{r['bess_net_base']}",
        # H
        "tolling_revenue": (
            f"={r['tolling_mw']}*{r['tolling_price']}*{r['tolling_factor']}/12/1000"
        ),
        # I — FCAS
        "fcas_regulation_revenue":    f"={r['dis_vol']}*{r['fcas_reg_price']}/1000",
        "fcas_contingency_revenue":   f"={r['dis_vol']}*{r['fcas_con_price']}/1000",
        "fcas_total_revenue":         f"={r['fcas_reg']}+{r['fcas_con']}",
        # J — VTA
        "vta_colocation_compensation":   f"=-{r['vta_coloc_pct']}*{r['vta_vol']}*{r['vta_price']}/1000",
        "vta_capacity_compensation":     f"=-{r['vta_cap_pct']}*{r['vta_vol']}*{r['vta_price']}/1000",
        "vta_degradation_compensation":  f"=-{r['vta_deg_pct']}*{r['vta_vol']}*{r['vta_price']}/1000",
        "vta_transmission_compensation": f"=-{r['vta_tx_pct']}*{r['vta_vol']}*{r['vta_price']}/1000",
        "vta_total_compensation":        f"={r['vta_coloc']}+{r['vta_cap']}+{r['vta_deg']}+{r['vta_tx']}",
        # G
        "gross_revenue":              f"={r['dis_rev']}+{r['multi_rev']}+{r['tolling_rev']}+{r['fcas_total']}",
        "total_costs": (
            f"={r['chg_cost']}+{r['import_costs']}+{r['export_costs']}-{r['sys_adj']}"
        ),
        "net_revenue":                f"={r['gross_rev']}-{r['total_costs']}+{r['vta_total']}",
    }
