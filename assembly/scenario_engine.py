"""
assembly/scenario_engine.py

Multi-scenario runner for ee-model-library.

Wraps engine.run() to execute named scenarios with parameter overrides,
collect KPIs, and produce sensitivity tables.

Two modes:
  - debt-not-resized: parameters change, debt stays same size
  - debt-resized: parameters change, debt auto-sizes to new CFADS

Source: Holsted Hybrid vendor model 'Sensi' tab.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Optional
from pydantic import BaseModel, Field

from assembly.engine import ProjectConfig, AssemblyResult, run


# ============================================================================
# DATA MODELS
# ============================================================================

class SensiParameter(BaseModel):
    """One sensitivity parameter to vary across scenarios."""
    name: str = Field(..., description="e.g. 'capex_delta', 'opex_delta'")
    param_type: str = Field(
        ..., description="'pct_delta' | 'absolute' | 'switch'"
    )
    target_field: str = Field(
        ..., description="Dot-path in ProjectConfig, e.g. 'capex.sensitivity_factor'"
    )
    values: list = Field(..., description="One value per scenario")


class ScenarioConfig(BaseModel):
    """Configuration for a multi-scenario run."""
    base_config: ProjectConfig
    scenario_names: list[str] = Field(default_factory=list)
    parameters: list[SensiParameter] = Field(default_factory=list)
    resize_debt: bool = Field(False, description="Re-size debt in each scenario")


class ScenarioKPIs(BaseModel):
    """Extracted KPIs from one scenario run."""
    name: str
    checks_pass: bool = True
    equity_irr: float = float("nan")
    project_irr: float = float("nan")
    ev_DKKk: float = 0.0
    equity_value_DKKk: float = 0.0
    total_revenue_lifetime: float = 0.0
    total_opex_lifetime: float = 0.0
    ebitda_lifetime: float = 0.0
    senior_debt_DKKk: float = 0.0
    gearing_pct: float = 0.0
    avg_dscr: float = float("nan")
    min_dscr: float = float("nan")
    avg_llcr: float = float("nan")
    min_llcr: float = float("nan")
    total_capex_DKKk: float = 0.0
    capex_per_mw: float = 0.0


class ScenarioResult(BaseModel):
    """Result from a multi-scenario run."""
    base_case: ScenarioKPIs
    scenarios: list[ScenarioKPIs] = Field(default_factory=list)
    tornado_data: list[dict] = Field(default_factory=list)


# ============================================================================
# HELPERS
# ============================================================================

def _get_nested(obj: Any, path: str) -> Any:
    """Get a nested attribute by dot-path."""
    parts = path.split(".")
    for part in parts:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        elif isinstance(obj, dict):
            obj = obj[part]
        else:
            raise AttributeError(f"Cannot resolve path {path!r} at {part!r}")
    return obj


def _set_nested(obj: Any, path: str, value: Any) -> Any:
    """Set a nested attribute by dot-path. Returns modified object (Pydantic model_copy)."""
    parts = path.split(".")
    if len(parts) == 1:
        if hasattr(obj, parts[0]):
            return obj.model_copy(update={parts[0]: value})
        return obj

    # Navigate to parent, then set on child
    parent_path = ".".join(parts[:-1])
    field = parts[-1]
    parent = _get_nested(obj, parent_path)
    if parent is None:
        return obj
    updated_parent = parent.model_copy(update={field: value})
    return _set_nested(obj, parent_path, updated_parent)


def _apply_parameter(config: ProjectConfig, param: SensiParameter, value: Any) -> ProjectConfig:
    """Apply a single parameter override to a ProjectConfig."""
    if param.param_type == "pct_delta":
        current = _get_nested(config, param.target_field)
        new_val = current * (1.0 + value)
        return _set_nested(config, param.target_field, new_val)
    elif param.param_type == "absolute":
        return _set_nested(config, param.target_field, value)
    elif param.param_type == "switch":
        return _set_nested(config, param.target_field, bool(value))
    else:
        raise ValueError(f"Unknown param_type: {param.param_type!r}")


def _extract_kpis(name: str, result: AssemblyResult) -> ScenarioKPIs:
    """Extract KPIs from an AssemblyResult."""
    out = result.outputs
    irr = out.get("IRR_001")
    capex = out.get("CAPEX_001")
    debt = out.get("DEBT_001")
    sculpt = out.get("DEBT_SCULPT_001")

    project_irr = irr.project_irr if irr else float("nan")
    equity_irr = irr.equity_irr if irr else float("nan")
    project_npv = irr.project_npv if irr and hasattr(irr, "project_npv") else 0.0

    total_capex = capex.cumulative_capex[-1] if capex and capex.cumulative_capex else 0.0

    if sculpt:
        senior = sculpt.total_debt
        gearing = sculpt.gearing
        avg_dscr = sculpt.avg_dscr
        min_dscr = sculpt.min_dscr
        avg_llcr = sculpt.llcr if hasattr(sculpt, "llcr") else float("nan")
        min_llcr = sculpt.min_llcr if hasattr(sculpt, "min_llcr") else float("nan")
    elif debt:
        senior = debt.opening_balance[0] + sum(debt.drawdown) if debt.drawdown else 0.0
        gearing = senior / total_capex if total_capex > 0 else 0.0
        avg_dscr = float("nan")
        min_dscr = debt.min_dscr
        avg_llcr = float("nan")
        min_llcr = float("nan")
    else:
        senior = 0.0
        gearing = 0.0
        avg_dscr = float("nan")
        min_dscr = float("nan")
        avg_llcr = float("nan")
        min_llcr = float("nan")

    # Revenue/OPEX lifetime
    rev_total = 0.0
    for mod_id in ("REV_001", "REV_002", "REV_003"):
        rev_out = out.get(mod_id)
        if rev_out and hasattr(rev_out, "net_revenue"):
            rev_total += sum(rev_out.net_revenue)
    opex_total = 0.0
    for mod_id in ("OPEX_001", "OPEX_002", "OPEX_003"):
        opex_out = out.get(mod_id)
        if opex_out and hasattr(opex_out, "total_opex"):
            opex_total += sum(opex_out.total_opex)

    return ScenarioKPIs(
        name=name,
        equity_irr=equity_irr,
        project_irr=project_irr,
        ev_DKKk=project_npv,
        total_revenue_lifetime=rev_total,
        total_opex_lifetime=opex_total,
        ebitda_lifetime=rev_total - opex_total,
        senior_debt_DKKk=senior,
        gearing_pct=gearing,
        avg_dscr=avg_dscr,
        min_dscr=min_dscr,
        avg_llcr=avg_llcr,
        min_llcr=min_llcr,
        total_capex_DKKk=total_capex,
        capex_per_mw=0.0,
    )


# ============================================================================
# CORE FUNCTION
# ============================================================================

def run_scenarios(config: ScenarioConfig) -> ScenarioResult:
    """
    Execute base case + all named scenarios and return collected KPIs.

    For each scenario:
      1. Deep-copy base config
      2. Apply parameter overrides
      3. engine.run()
      4. Extract KPIs
    """
    # Run base case
    base_result = run(config.base_config)
    base_kpis = _extract_kpis("Base Case", base_result)

    n_scenarios = len(config.scenario_names)
    scenarios: list[ScenarioKPIs] = []

    for i, name in enumerate(config.scenario_names):
        # Deep copy via model_copy
        scenario_config = config.base_config.model_copy(deep=True)

        # Apply each parameter's i-th value
        for param in config.parameters:
            if i < len(param.values):
                scenario_config = _apply_parameter(scenario_config, param, param.values[i])

        # Run
        scenario_result = run(scenario_config)
        kpis = _extract_kpis(name, scenario_result)
        scenarios.append(kpis)

    # Build tornado data (pairs: even=down, odd=up)
    tornado: list[dict] = []
    for param in config.parameters:
        if len(param.values) >= 2:
            tornado.append({
                "parameter": param.name,
                "base_irr": base_kpis.project_irr,
                "low_irr": scenarios[0].project_irr if scenarios else float("nan"),
                "high_irr": scenarios[1].project_irr if len(scenarios) > 1 else float("nan"),
            })

    return ScenarioResult(
        base_case=base_kpis,
        scenarios=scenarios,
        tornado_data=tornado,
    )
