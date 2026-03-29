"""
calibration/deal_db.py

Shared utility for logging calibration results to a deal database.
Each call appends one entry to calibration/deal_db.json.

Usage:
    from calibration.deal_db import save_to_deal_db
    save_to_deal_db("Skuodas", config, result, targets=TARGETS)
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel


DEFAULT_PATH = str(Path(__file__).parent / "deal_db.json")


# ============================================================================
# SCALAR EXTRACTION
# ============================================================================

def _is_scalar(v: Any) -> bool:
    """True for values that belong in the deal DB (not time-series)."""
    if v is None:
        return True
    if isinstance(v, (int, float, bool, str)):
        return True
    return False


def _extract_scalars(obj: Any, prefix: str = "") -> dict:
    """
    Walk a Pydantic model (or dict) and extract all scalar fields.
    Skips lists/arrays (time-series), nested models are recursed with dotted keys.
    """
    result = {}
    if obj is None:
        return result

    if isinstance(obj, BaseModel):
        items = obj.model_fields.keys()
        getter = lambda k: getattr(obj, k)
    elif isinstance(obj, dict):
        items = obj.keys()
        getter = lambda k: obj[k]
    else:
        return result

    for key in items:
        val = getter(key)
        full_key = f"{prefix}.{key}" if prefix else key
        if _is_scalar(val):
            if isinstance(val, float) and math.isnan(val):
                result[full_key] = None
            else:
                result[full_key] = val
        elif isinstance(val, BaseModel):
            result.update(_extract_scalars(val, full_key))

    return result


# ============================================================================
# KPI EXTRACTION
# ============================================================================

def _extract_kpis(result: Any) -> dict:
    """Extract standard KPIs from AssemblyResult.outputs."""
    out = result.outputs
    kpis = {}

    irr = out.get("IRR_001")
    if irr:
        kpis["project_irr"] = irr.project_irr
        kpis["equity_irr"] = irr.equity_irr
        kpis["project_npv"] = irr.project_npv
        kpis["equity_npv"] = irr.equity_npv

    for debt_id in ("DEBT_SCULPT_001", "DEBT_001"):
        d = out.get(debt_id)
        if d:
            kpis["senior_debt"] = getattr(d, "total_debt", None) or getattr(d, "facility", None)
            if hasattr(d, "gearing"):
                kpis["gearing"] = d.gearing
            for attr in ("min_dscr", "min_dscr_6m", "min_dscr_12m", "min_llcr", "fully_repaid"):
                if hasattr(d, attr):
                    kpis[attr] = getattr(d, attr)
            if hasattr(d, "interest"):
                kpis["total_interest"] = sum(d.interest)
            elif hasattr(d, "total_interest"):
                kpis["total_interest"] = d.total_interest
            break

    # Revenue
    total_rev = 0.0
    for rev_id in ("REV_001", "REV_002", "REV_003"):
        r = out.get(rev_id)
        if r:
            rev_sum = sum(r.net_revenue)
            kpis[f"{rev_id}_net_revenue"] = rev_sum
            total_rev += rev_sum
    kpis["total_net_revenue"] = total_rev

    # OPEX
    total_opex = 0.0
    for opex_id in ("OPEX_001", "OPEX_002", "OPEX_003"):
        o = out.get(opex_id)
        if o:
            opex_sum = o.total_opex_lifetime
            kpis[f"{opex_id}_lifetime"] = opex_sum
            total_opex += opex_sum
    kpis["total_opex"] = total_opex

    # CAPEX
    capex = out.get("CAPEX_001")
    if capex:
        kpis["total_capex"] = sum(capex.total_capex_monthly)

    # BS
    bs = out.get("BS_001")
    if bs:
        kpis["bs_max_imbalance"] = max(abs(v) for v in bs.imbalance)

    # Tax
    for tax_id in ("TAX_001", "TAX_DE_001", "TAX_LT_001"):
        t = out.get(tax_id)
        if t:
            kpis["total_tax_paid"] = t.total_tax_paid
            break

    # WACC
    wacc = out.get("WACC_001")
    if wacc:
        kpis["wacc"] = wacc.wacc

    # SHL
    shl = out.get("SHL_001")
    if shl:
        kpis["shl_initial"] = shl.initial_shl
        kpis["shl_peak"] = max(shl.closing_balance)

    # DIV
    div = out.get("DIV_001")
    if div:
        kpis["total_dividends"] = div.total_dividends

    # Sanitise NaN
    for k, v in list(kpis.items()):
        if isinstance(v, float) and math.isnan(v):
            kpis[k] = None

    return kpis


# ============================================================================
# TARGET COMPARISON
# ============================================================================

def _compare_targets(kpis: dict, targets: dict) -> list[dict]:
    """Compare KPIs against target ranges. Returns list of check results."""
    checks = []

    # Map target names to KPI keys
    key_map = {
        "Project IRR": "project_irr",
        "Equity IRR": "equity_irr",
        "Senior debt": "senior_debt",
        "Min DSCR": "min_dscr",
        "Min DSCR 6m": "min_dscr_6m",
        "Min DSCR 12m": "min_dscr_12m",
        "Min LLCR": "min_llcr",
        "Total revenue": "total_net_revenue",
        "Total OPEX": "total_opex",
        "BS max imb": "bs_max_imbalance",
    }

    for name, target_val in targets.items():
        kpi_key = key_map.get(name)
        actual = kpis.get(kpi_key) if kpi_key else None

        if isinstance(target_val, (list, tuple)) and len(target_val) == 2:
            lo, hi = target_val
            ok = lo <= actual <= hi if actual is not None else False
        else:
            # Single target value — just record
            lo = hi = target_val
            ok = None

        checks.append({
            "name": name,
            "target_lo": lo,
            "target_hi": hi,
            "actual": actual,
            "pass": ok,
        })

    return checks


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def save_to_deal_db(
    deal_name: str,
    config: Any,
    result: Any,
    targets: Optional[dict] = None,
    metadata: Optional[dict] = None,
    path: str = DEFAULT_PATH,
) -> dict:
    """
    Append a calibration entry to the deal database.

    Args:
        deal_name: Human-readable deal name
        config: ProjectConfig used for the run
        result: AssemblyResult from engine.run() or debt_solver
        targets: Dict of target KPIs {name: (lo, hi) or scalar}
        metadata: Extra fields (source_model, notes, etc.)
        path: Path to deal_db.json

    Returns:
        The entry dict that was appended.
    """
    kpis = _extract_kpis(result)
    checks = _compare_targets(kpis, targets) if targets else []
    all_pass = all(c["pass"] for c in checks if c["pass"] is not None) if checks else None

    entry = {
        "deal_name": deal_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "market": config.market,
        "technology": config.technology,
        "project_name": config.project_name,
        "periods": config.timeline.periods,
        "start_year": config.timeline.start_year,
        "start_month": config.timeline.start_month,
        "currency": config.timeline.currency,
        "modules_run": list(result.outputs.keys()),
        "assumptions": _extract_scalars(config),
        "kpis": kpis,
        "checks": checks,
        "all_pass": all_pass,
        "warnings_count": len(result.warnings),
    }
    if metadata:
        entry["metadata"] = metadata

    # Load existing DB or start fresh
    if os.path.exists(path):
        with open(path, "r") as f:
            db = json.load(f)
    else:
        db = []

    db.append(entry)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(db, f, indent=2, default=str)

    print(f"Deal DB: saved '{deal_name}' ({len(kpis)} KPIs, "
          f"{'ALL PASS' if all_pass else 'FAIL' if all_pass is False else 'no targets'})"
          f" -> {path} ({len(db)} deals)")

    return entry
