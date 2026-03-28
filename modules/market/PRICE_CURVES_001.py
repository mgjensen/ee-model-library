"""
MODULE_ID:    PRICE_CURVES_001
VERSION:      1.0
TIER:         both
MARKETS:      ["*"]
TECHNOLOGIES: ["*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-17

Market price curve library.
Stores named monthly/annual price curves and serves them by name to downstream
modules. Handles timeline alignment, annual-to-monthly spread, extrapolation,
and inflation index construction.

Source: EE_MODEL_BUILD_SPEC.md v2.0 §4
"""

from __future__ import annotations

from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# DATA MODELS
# ============================================================================

class PriceCurve(BaseModel):
    """A named time-series of prices or rates."""
    name: str
    category: str
    unit: str
    frequency: str = Field(
        "monthly",
        description="'monthly' or 'annual'"
    )
    source: str = ""
    start_year: int = 2025
    start_month: int = Field(1, ge=1, le=12)
    values: list[float]


class CurveLibrary(BaseModel):
    """Container for named price curves."""
    curves: dict[str, PriceCurve] = Field(default_factory=dict)


# ============================================================================
# INPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0, description="Total project life in months")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # Curve library — named curves for lookup
    library: CurveLibrary = Field(default_factory=CurveLibrary)

    # Strict mode: raise ValueError on missing curve names instead of returning zeros
    strict_lookup: bool = Field(
        False, description="If True, missing curve names raise ValueError with available names"
    )

    # Selection fields — curve name to look up in library.curves
    capture_price_curve: str = Field("", description="Curve name for capture price")
    bess_revenue_curve: str = Field("", description="Curve name for BESS revenue")
    wholesale_curve: str = Field("", description="Curve name for wholesale price")
    interest_rate_curve: str = Field("", description="Curve name for interest rate")
    inflation_power_curve: str = Field("", description="Curve name for power inflation rate")
    inflation_ppa_curve: str = Field("", description="Curve name for PPA inflation rate")
    inflation_goo_curve: str = Field("", description="Curve name for GoO inflation rate")
    inflation_om_curve: str = Field("", description="Curve name for O&M inflation rate")
    inflation_land_curve: str = Field("", description="Curve name for land lease inflation rate")
    inflation_opex_curve: str = Field("", description="Curve name for general OPEX inflation rate")
    goo_curve: str = Field("", description="Curve name for GoO price")
    curtailment_curve: str = Field("", description="Curve name for curtailment factor")
    imbalance_curve: str = Field("", description="Curve name for imbalance cost")
    bess_opex_curve: str = Field("", description="Curve name for BESS OPEX")
    capture_price_curve_debt: str = Field("", description="Curve name for capture price (debt case)")
    bess_revenue_curve_debt: str = Field("", description="Curve name for BESS revenue (debt case)")


# ============================================================================
# OUTPUTS
# ============================================================================

class Outputs(BaseModel):
    # Direct price curves aligned to project timeline (length = periods)
    capture_price_monthly: list[float]
    bess_revenue_monthly: list[float]
    wholesale_price_monthly: list[float]
    interest_rate_monthly: list[float]
    goo_price_monthly: list[float]
    curtailment_monthly: list[float]
    imbalance_cost_monthly: list[float]
    bess_opex_monthly: list[float]

    # Inflation indices (length = periods, start at 1.0)
    inflation_power_index: list[float]
    inflation_ppa_index: list[float]
    inflation_goo_index: list[float]
    inflation_om_index: list[float]
    inflation_land_index: list[float]
    inflation_opex_index: list[float]

    # Debt-case curves (length = periods)
    capture_price_debt_monthly: list[float]
    bess_revenue_debt_monthly: list[float]

    # Metadata
    selected_curves: dict[str, str]
    missing_curves: list[str]


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    groups: OrderedDict[int, list[int]] = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _align_curve(curve: PriceCurve, periods: int, start_year: int, start_month: int) -> list[float]:
    """Align a PriceCurve to the project timeline.

    - Annual curves: each value is repeated for 12 months.
    - Offset: project start vs curve start determines where to slice.
    - Padding: if curve is shorter than periods, extrapolate last value.
    """
    if curve.frequency == "annual":
        # Expand annual values to monthly
        monthly_vals: list[float] = []
        for v in curve.values:
            monthly_vals.extend([v] * 12)
        # Treat as monthly starting Jan of curve start_year
        c_start_abs = curve.start_year * 12  # January of start_year
    else:
        monthly_vals = list(curve.values)
        c_start_abs = curve.start_year * 12 + (curve.start_month - 1)

    p_start_abs = start_year * 12 + (start_month - 1)
    offset = p_start_abs - c_start_abs  # how many months into the curve

    result: list[float] = []
    last_val = monthly_vals[-1] if monthly_vals else 0.0
    for p in range(periods):
        idx = offset + p
        if idx < 0:
            result.append(0.0)
        elif idx < len(monthly_vals):
            result.append(monthly_vals[idx])
        else:
            result.append(last_val)
    return result


def _build_inflation_index(rates: list[float], periods: int, start_year: int, start_month: int) -> list[float]:
    """Build a compounding inflation index from annual rate curve.

    rates is already aligned to the project timeline (monthly).
    The index starts at 1.0 in the first period and compounds annually.
    Within a calendar year, the index stays flat (steps at year boundary).
    """
    yg = _year_groups(periods, start_year, start_month)
    index = [0.0] * periods
    cumulative = 1.0
    first_year = True
    for _year, month_indices in yg.items():
        if first_year:
            for p in month_indices:
                index[p] = cumulative
            first_year = False
        else:
            # Use the rate from the first month of this year
            rate = rates[month_indices[0]] if month_indices else 0.0
            cumulative *= (1.0 + rate)
            for p in month_indices:
                index[p] = cumulative
    return index


def _resolve_curve(name: str, library: CurveLibrary, periods: int,
                   start_year: int, start_month: int,
                   missing: list[str], strict: bool = False) -> list[float]:
    """Look up a curve by name; return zeros if empty or missing.

    In strict mode, missing names raise ValueError listing available curves.
    """
    if not name:
        return [0.0] * periods
    if name not in library.curves:
        if strict:
            available = sorted(library.curves.keys()) if library.curves else ["(none)"]
            raise ValueError(
                f"Curve '{name}' not found in library. "
                f"Available curves: {', '.join(available)}"
            )
        missing.append(name)
        return [0.0] * periods
    return _align_curve(library.curves[name], periods, start_year, start_month)


# ============================================================================
# CALCULATE
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Resolve all selected curves and build inflation indices."""
    n = inputs.periods
    sy, sm = inputs.start_year, inputs.start_month
    lib = inputs.library
    missing: list[str] = []
    strict = inputs.strict_lookup

    # Direct price curves
    capture_price = _resolve_curve(inputs.capture_price_curve, lib, n, sy, sm, missing, strict)
    bess_revenue = _resolve_curve(inputs.bess_revenue_curve, lib, n, sy, sm, missing, strict)
    wholesale = _resolve_curve(inputs.wholesale_curve, lib, n, sy, sm, missing, strict)
    interest_rate = _resolve_curve(inputs.interest_rate_curve, lib, n, sy, sm, missing, strict)
    goo_price = _resolve_curve(inputs.goo_curve, lib, n, sy, sm, missing, strict)
    curtailment = _resolve_curve(inputs.curtailment_curve, lib, n, sy, sm, missing, strict)
    imbalance_cost = _resolve_curve(inputs.imbalance_curve, lib, n, sy, sm, missing, strict)
    bess_opex = _resolve_curve(inputs.bess_opex_curve, lib, n, sy, sm, missing, strict)

    # Debt-case curves
    capture_debt = _resolve_curve(inputs.capture_price_curve_debt, lib, n, sy, sm, missing, strict)
    bess_debt = _resolve_curve(inputs.bess_revenue_curve_debt, lib, n, sy, sm, missing, strict)

    # Inflation rate curves (resolve monthly rates, then build indices)
    infl_power_rates = _resolve_curve(inputs.inflation_power_curve, lib, n, sy, sm, missing, strict)
    infl_ppa_rates = _resolve_curve(inputs.inflation_ppa_curve, lib, n, sy, sm, missing, strict)
    infl_goo_rates = _resolve_curve(inputs.inflation_goo_curve, lib, n, sy, sm, missing, strict)
    infl_om_rates = _resolve_curve(inputs.inflation_om_curve, lib, n, sy, sm, missing, strict)
    infl_land_rates = _resolve_curve(inputs.inflation_land_curve, lib, n, sy, sm, missing, strict)
    infl_opex_rates = _resolve_curve(inputs.inflation_opex_curve, lib, n, sy, sm, missing, strict)

    infl_power_idx = _build_inflation_index(infl_power_rates, n, sy, sm)
    infl_ppa_idx = _build_inflation_index(infl_ppa_rates, n, sy, sm)
    infl_goo_idx = _build_inflation_index(infl_goo_rates, n, sy, sm)
    infl_om_idx = _build_inflation_index(infl_om_rates, n, sy, sm)
    infl_land_idx = _build_inflation_index(infl_land_rates, n, sy, sm)
    infl_opex_idx = _build_inflation_index(infl_opex_rates, n, sy, sm)

    # Build selected_curves metadata
    selection_map = {
        "capture_price": inputs.capture_price_curve,
        "bess_revenue": inputs.bess_revenue_curve,
        "wholesale": inputs.wholesale_curve,
        "interest_rate": inputs.interest_rate_curve,
        "inflation_power": inputs.inflation_power_curve,
        "inflation_ppa": inputs.inflation_ppa_curve,
        "inflation_goo": inputs.inflation_goo_curve,
        "inflation_om": inputs.inflation_om_curve,
        "inflation_land": inputs.inflation_land_curve,
        "inflation_opex": inputs.inflation_opex_curve,
        "goo": inputs.goo_curve,
        "curtailment": inputs.curtailment_curve,
        "imbalance": inputs.imbalance_curve,
        "bess_opex": inputs.bess_opex_curve,
        "capture_price_debt": inputs.capture_price_curve_debt,
        "bess_revenue_debt": inputs.bess_revenue_curve_debt,
    }
    selected = {k: v for k, v in selection_map.items() if v}

    return Outputs(
        capture_price_monthly=capture_price,
        bess_revenue_monthly=bess_revenue,
        wholesale_price_monthly=wholesale,
        interest_rate_monthly=interest_rate,
        goo_price_monthly=goo_price,
        curtailment_monthly=curtailment,
        imbalance_cost_monthly=imbalance_cost,
        bess_opex_monthly=bess_opex,
        inflation_power_index=infl_power_idx,
        inflation_ppa_index=infl_ppa_idx,
        inflation_goo_index=infl_goo_idx,
        inflation_om_index=infl_om_idx,
        inflation_land_index=infl_land_idx,
        inflation_opex_index=infl_opex_idx,
        capture_price_debt_monthly=capture_debt,
        bess_revenue_debt_monthly=bess_debt,
        selected_curves=selected,
        missing_curves=missing,
    )


# ============================================================================
# EXCEL FORMULAS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows.

    refs should map output field names to cell reference strings.
    For PRICE_CURVES_001, formulas are simple lookups from the curve library
    sheet — downstream modules reference these rows directly.
    """
    formulas = {}
    for field in (
        "capture_price_monthly", "bess_revenue_monthly",
        "wholesale_price_monthly", "interest_rate_monthly",
        "goo_price_monthly", "curtailment_monthly",
        "imbalance_cost_monthly", "bess_opex_monthly",
        "capture_price_debt_monthly", "bess_revenue_debt_monthly",
    ):
        if field in refs:
            formulas[field] = f"=CurveLibrary!{refs[field]}"
    for field in (
        "inflation_power_index", "inflation_ppa_index",
        "inflation_goo_index", "inflation_om_index",
        "inflation_land_index", "inflation_opex_index",
    ):
        if field in refs:
            formulas[field] = f"=PrevYear*({refs.get(field + '_rate', '0')}+1)"
    return formulas
