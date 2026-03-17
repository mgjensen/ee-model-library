"""
assembly/scenario_runner.py

Multi-scenario runner, portfolio aggregation, and sensitivity tables.

Wraps engine.run() to execute named scenarios with parameter overrides,
extract KPIs, aggregate portfolio metrics, and generate 2D sensitivity grids.

Source: Holsted Hybrid vendor model Dashboard + Sensi tabs.
"""

from __future__ import annotations

import math
from typing import Any, Optional
from pydantic import BaseModel, Field

from assembly.engine import ProjectConfig, AssemblyResult, run


# ============================================================================
# DATA MODELS
# ============================================================================

class ScenarioOverride(BaseModel):
    """One parameter override for a scenario."""
    path: str = Field(..., description="Dot-separated path into ProjectConfig")
    value: Any


class ScenarioConfig(BaseModel):
    """Named scenario with parameter overrides."""
    name: str
    overrides: list[ScenarioOverride] = Field(default_factory=list)


class ScenarioResult(BaseModel):
    """Result from one scenario run."""
    model_config = {"arbitrary_types_allowed": True}
    name: str
    result: Optional[AssemblyResult] = None
    kpis: dict = Field(default_factory=dict)


class PortfolioResult(BaseModel):
    """Aggregated portfolio view across multiple projects."""
    model_config = {"arbitrary_types_allowed": True}
    scenario_results: list[ScenarioResult]
    portfolio_kpis: dict = Field(default_factory=dict)


class SensitivityAxis(BaseModel):
    """One axis of a 2D sensitivity table."""
    label: str
    path: str
    values: list = Field(default_factory=list)


# ============================================================================
# HELPERS
# ============================================================================

def _get_nested(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict):
            obj = obj[part]
        else:
            raise AttributeError(f"Cannot resolve {path!r} at {part!r}")
    return obj


def _set_nested(obj: Any, path: str, value: Any) -> Any:
    parts = path.split(".")
    if len(parts) == 1:
        if hasattr(obj, parts[0]):
            return obj.model_copy(update={parts[0]: value})
        return obj
    parent = _get_nested(obj, ".".join(parts[:-1]))
    if parent is None:
        return obj
    updated = parent.model_copy(update={parts[-1]: value})
    return _set_nested(obj, ".".join(parts[:-1]), updated)


def _extract_kpis(result: AssemblyResult) -> dict:
    """Extract standard KPIs from an AssemblyResult."""
    out = result.outputs
    irr = out.get("IRR_001")
    wacc = out.get("WACC_001")
    sculpt = out.get("DEBT_SCULPT_001")
    debt = out.get("DEBT_001")
    capex = out.get("CAPEX_001")
    bs = out.get("BS_001")

    kpis = {}
    kpis["project_irr"] = irr.project_irr if irr else float("nan")
    kpis["equity_irr"] = irr.equity_irr if irr else float("nan")
    kpis["project_npv"] = irr.project_npv if irr and hasattr(irr, "project_npv") else 0.0
    kpis["equity_npv"] = irr.equity_npv if irr and hasattr(irr, "equity_npv") else 0.0
    kpis["wacc"] = wacc.wacc if wacc else 0.0

    if sculpt:
        kpis["senior_debt"] = sculpt.total_debt
        kpis["gearing"] = sculpt.gearing
        kpis["min_dscr"] = sculpt.min_dscr
        kpis["avg_dscr"] = sculpt.avg_dscr
        kpis["min_llcr"] = sculpt.min_llcr if hasattr(sculpt, "min_llcr") else float("nan")
        kpis["min_dscr_6m"] = sculpt.min_dscr_6m if hasattr(sculpt, "min_dscr_6m") else float("nan")
    elif debt:
        kpis["senior_debt"] = sum(debt.drawdown)
        kpis["gearing"] = 0.0
        kpis["min_dscr"] = debt.min_dscr
    else:
        kpis["senior_debt"] = 0.0
        kpis["gearing"] = 0.0
        kpis["min_dscr"] = float("nan")

    total_capex = capex.cumulative_capex[-1] if capex and capex.cumulative_capex else 0.0
    kpis["total_capex"] = total_capex
    kpis["ev"] = kpis.get("project_npv", 0.0)

    # Revenue/OPEX lifetime
    rev_total = sum(
        sum(out[m].net_revenue) for m in ("REV_001", "REV_002", "REV_003") if m in out
    )
    opex_total = sum(
        sum(out[m].total_opex) for m in ("OPEX_001", "OPEX_002", "OPEX_003") if m in out
    )
    kpis["total_revenue"] = rev_total
    kpis["total_opex"] = opex_total
    kpis["ebitda"] = rev_total - opex_total

    return kpis


# ============================================================================
# SCENARIO RUNNER
# ============================================================================

def run_scenarios(
    base: ProjectConfig,
    scenarios: list[ScenarioConfig],
) -> list[ScenarioResult]:
    """Run base config with each scenario's overrides. Return all results."""
    results = []
    for sc in scenarios:
        cfg = base.model_copy(deep=True)
        for ov in sc.overrides:
            cfg = _set_nested(cfg, ov.path, ov.value)
        ar = run(cfg)
        kpis = _extract_kpis(ar)
        results.append(ScenarioResult(name=sc.name, result=ar, kpis=kpis))
    return results


# ============================================================================
# PORTFOLIO AGGREGATION
# ============================================================================

def aggregate_portfolio(
    results: list[ScenarioResult],
    weights: list[float] | None = None,
) -> PortfolioResult:
    """Aggregate multiple project results into a portfolio view."""
    if not results:
        return PortfolioResult(scenario_results=[], portfolio_kpis={})

    if weights is None:
        capex_list = [r.kpis.get("total_capex", 0.0) for r in results]
        total_capex = sum(capex_list)
        weights = [c / total_capex if total_capex > 0 else 1 / len(results) for c in capex_list]

    pk = {}
    # Weighted averages
    for key in ("project_irr", "equity_irr", "wacc"):
        vals = [r.kpis.get(key, 0.0) for r in results]
        if any(math.isnan(v) for v in vals):
            pk[key] = float("nan")
        else:
            pk[key] = sum(v * w for v, w in zip(vals, weights))

    # Sums
    for key in ("ev", "senior_debt", "total_capex", "total_revenue", "total_opex", "ebitda"):
        pk[key] = sum(r.kpis.get(key, 0.0) for r in results)

    # Derived
    total_uses = pk["total_capex"]
    pk["total_equity"] = total_uses - pk["senior_debt"]
    pk["gearing"] = pk["senior_debt"] / total_uses if total_uses > 0 else 0.0

    return PortfolioResult(scenario_results=results, portfolio_kpis=pk)


# ============================================================================
# SENSITIVITY TABLE
# ============================================================================

def run_sensitivity_table(
    base: ProjectConfig,
    row_axis: SensitivityAxis,
    col_axis: SensitivityAxis,
    kpi: str = "project_irr",
) -> dict:
    """Run a 2D sensitivity grid. Return {(row_val, col_val): kpi_value}."""
    grid = {}
    for rv in row_axis.values:
        for cv in col_axis.values:
            cfg = base.model_copy(deep=True)
            cfg = _set_nested(cfg, row_axis.path, rv)
            cfg = _set_nested(cfg, col_axis.path, cv)
            ar = run(cfg)
            kpis = _extract_kpis(ar)
            grid[(rv, cv)] = kpis.get(kpi, float("nan"))
    return grid
