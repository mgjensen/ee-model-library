"""
assembly/quarterly.py

Quarterly aggregation utility.

Takes a monthly AssemblyResult and produces quarterly aggregated outputs.
Flow items (revenue, opex, interest): sum of 3 months.
Stock items (balance sheet, debt balance): end-of-quarter value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from assembly.engine import AssemblyResult


# ============================================================================
# QUARTERLY RESULT
# ============================================================================

@dataclass
class QuarterlyResult:
    """Quarterly aggregated outputs."""
    project_name: str
    n_quarters: int
    start_year: int
    start_month: int
    quarter_labels: list[str]   # ["Q1 2026", "Q2 2026", ...]
    outputs: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# FLOW vs STOCK CLASSIFICATION
# ============================================================================

# Fields that are stock items (take end-of-quarter value)
STOCK_FIELDS = {
    "opening_balance", "closing_balance", "cumulative_capex",
    "debt_balance", "cash", "total_assets", "total_equity",
    "total_liabilities_equity", "fixed_assets_gross", "fixed_assets_net",
    "accumulated_depreciation", "contributed_equity", "retained_earnings",
    "closing_cash", "opening_cash", "facility_balance",
    "tax_asset_base_eop", "accounting_asset_base_eop",
    "balance", "actual_balance", "target_balance",
    "fund_balance", "provision_balance",
    "cumulative_swept", "idc_cumulative",
}


# ============================================================================
# CORE FUNCTION
# ============================================================================

def quarterly_aggregate(result: AssemblyResult) -> QuarterlyResult:
    """
    Aggregate monthly AssemblyResult to quarterly periods.

    Flow items: sum 3 months per quarter.
    Stock items: take end-of-quarter (last month) value.
    Scalars: pass through unchanged.
    """
    n = result.periods
    sm = result.start_month
    sy = result.start_year

    # Build quarter groupings
    quarters: list[list[int]] = []
    current_q: list[int] = []
    for p in range(n):
        current_q.append(p)
        cal_month = ((sm - 1 + p) % 12) + 1
        if cal_month in (3, 6, 9, 12):
            quarters.append(current_q)
            current_q = []
    if current_q:
        quarters.append(current_q)

    nq = len(quarters)

    # Quarter labels
    labels = []
    for q_months in quarters:
        last_p = q_months[-1]
        offset = sm - 1 + last_p
        y = sy + offset // 12
        m = (offset % 12) + 1
        qn = (m - 1) // 3 + 1
        labels.append(f"Q{qn} {y}")

    qr = QuarterlyResult(
        project_name=result.project_name,
        n_quarters=nq,
        start_year=sy,
        start_month=sm,
        quarter_labels=labels,
    )

    # Aggregate each module's outputs
    for mod_id, mod_out in result.outputs.items():
        if mod_out is None:
            continue
        q_mod: dict[str, Any] = {}
        for fname in mod_out.model_fields:
            val = getattr(mod_out, fname)
            if isinstance(val, list) and len(val) == n and all(isinstance(v, (int, float)) for v in val):
                is_stock = fname in STOCK_FIELDS
                q_series = []
                for q_months in quarters:
                    if is_stock:
                        q_series.append(val[q_months[-1]])
                    else:
                        q_series.append(sum(val[p] for p in q_months))
                q_mod[fname] = q_series
            elif isinstance(val, (int, float, bool, str)):
                q_mod[fname] = val  # pass through scalars
        qr.outputs[mod_id] = q_mod

    return qr
