"""
assembly/excel_writer.py

Writes an AssemblyResult to a standard 5-sheet .xlsx workbook using openpyxl.

Sheet layout:
    "Revenue"    — REV_001, REV_002
    "Costs"      — OPEX_001, OPEX_002, CAPEX_001
    "Debt"       — DEBT_001, TAX_001, WACC_001 (scalars in col B)
    "Statements" — PL_001, CF_001, BS_001, IRR_001
    "Summary"    — scalar KPIs drawn from IRR_001, DEBT_001, PL_001, CAPEX_001

Row/column conventions:
    Row 1 = project name / sheet title
    Row 2 = blank
    Row 3 = period indices (0, 1, 2, ...) in cols C+
    Row 4 = date labels ("Jan-2026") in cols C+
    Row 5+ = data rows per ROW_MAP

    Col A = field label
    Col B = units string (or scalar value for WACC_001 fields)
    Col C = period 0, Col D = period 1, ...
"""

from __future__ import annotations

import math
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from assembly.cell_mapper import (
    ROW_MAP,
    MODULE_SHEET,
    FIELD_LABELS,
    FIELD_UNITS,
    FIELD_UNITS_DEFAULT,
    get_label,
    get_units,
    period_col,
    col_letter,
)
from assembly.engine import AssemblyResult, ProjectConfig

# ============================================================================
# CONSTANTS
# ============================================================================

SHEETS = ["Revenue", "Costs", "Debt", "Statements", "Summary"]

# WACC_001 scalar fields — written to col B on Debt sheet (no time-series columns)
WACC_SCALAR_FIELDS = [
    "wacc",
    "blended_cost_of_equity",
    "ppa_cost_of_equity",
    "merchant_cost_of_equity",
    "cost_of_debt_posttax",
    "levered_beta",
    "debt_to_equity_ratio",
    "ppa_erp",
    "merchant_erp",
]

# IRR_001 scalar fields → Summary tab
IRR_SCALAR_FIELDS = [
    "project_irr",
    "equity_irr",
    "project_npv",
    "equity_npv",
    "payback_period_months",
]

# Number formats
FMT_DKKK    = '#,##0.00'
FMT_PCT     = '0.00%'
FMT_RATIO   = '0.000'
FMT_INTEGER = '#,##0'
FMT_GENERAL = 'General'

_PCT_FIELDS   = {"wacc", "blended_cost_of_equity", "ppa_cost_of_equity",
                 "merchant_cost_of_equity", "cost_of_debt_posttax",
                 "ppa_erp", "merchant_erp", "project_irr", "equity_irr"}
_RATIO_FIELDS = {"levered_beta", "debt_to_equity_ratio", "dscr_annual",
                 "price_indexation_factor", "discharge_indexation_factor",
                 "charge_indexation_factor"}
_INT_FIELDS   = {"payback_period_months"}
_MWH_FIELDS   = {"net_production_MWh", "discharge_volume", "goo_volume",
                 "goo_lost_volume", "total_ppa_contracted_volume"}

_HEADER_FILL  = PatternFill("solid", fgColor="1F4E79")
_TITLE_FILL   = PatternFill("solid", fgColor="2E75B6")
_WHITE_FONT   = Font(color="FFFFFF", bold=True)
_BOLD_FONT    = Font(bold=True)


# ============================================================================
# HELPERS
# ============================================================================

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _period_to_label(start_year: int, start_month: int, period: int) -> str:
    """Return 'Jan-2026' label for period p given project start."""
    total_months = (start_month - 1) + period
    year  = start_year + total_months // 12
    month = total_months % 12
    return f"{_MONTH_NAMES[month]}-{year}"


def _safe(value: Any) -> Any:
    """Replace NaN/inf with empty string so Excel accepts the cell value."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    return value


def _num_format(field_name: str) -> str:
    if field_name in _PCT_FIELDS:
        return FMT_PCT
    if field_name in _RATIO_FIELDS:
        return FMT_RATIO
    if field_name in _INT_FIELDS:
        return FMT_INTEGER
    return FMT_DKKK


def _write_header_rows(ws, project_name: str, n: int,
                       start_year: int, start_month: int) -> None:
    """Write rows 1–4: title, blank, period indices, date labels."""
    # Row 1: title
    cell = ws.cell(row=1, column=1, value=project_name)
    cell.font = _WHITE_FONT
    cell.fill = _TITLE_FILL
    ws.cell(row=1, column=2, value=ws.title).font = _WHITE_FONT
    ws.cell(row=1, column=2).fill = _TITLE_FILL

    # Row 3: period indices
    ws.cell(row=3, column=1, value="Period").font = _BOLD_FONT
    for p in range(n):
        c = period_col(p)
        cell = ws.cell(row=3, column=c, value=p)
        cell.fill = _HEADER_FILL
        cell.font = _WHITE_FONT
        cell.alignment = Alignment(horizontal="center")

    # Row 4: date labels
    ws.cell(row=4, column=1, value="Date").font = _BOLD_FONT
    for p in range(n):
        c = period_col(p)
        cell = ws.cell(row=4, column=c, value=_period_to_label(start_year, start_month, p))
        cell.fill = _HEADER_FILL
        cell.font = _WHITE_FONT
        cell.alignment = Alignment(horizontal="center")


def _get_field_value(module_out: Any, field_name: str) -> Any:
    """Return the attribute named field_name from a module output object."""
    return getattr(module_out, field_name, None)


# ============================================================================
# SHEET WRITERS
# ============================================================================

def _write_time_series_rows(ws, result: AssemblyResult) -> None:
    """
    Write all ROW_MAP entries that belong to this worksheet as time-series rows.
    Skips WACC_001 (handled separately) and rows for disabled modules.
    """
    sheet_name = ws.title
    # Collect (row, module_id, field_name) sorted by row
    entries = [
        (row, mid, field)
        for (sh, mid, field), row in ROW_MAP.items()
        if sh == sheet_name and mid != "WACC_001"
    ]
    entries.sort()

    for row_num, module_id, field_name in entries:
        module_out = result.outputs.get(module_id)

        # Label col A
        label_cell = ws.cell(row=row_num, column=1, value=get_label(field_name))
        label_cell.font = _BOLD_FONT

        # Units col B
        ws.cell(row=row_num, column=2, value=get_units(field_name))

        if module_out is None:
            continue  # module disabled — leave data cells empty

        value = _get_field_value(module_out, field_name)
        if value is None:
            continue

        fmt = _num_format(field_name)

        if isinstance(value, list):
            for p, v in enumerate(value):
                c = period_col(p)
                cell = ws.cell(row=row_num, column=c, value=_safe(v))
                cell.number_format = fmt
        else:
            # Scalar on a time-series row — write to col B
            cell = ws.cell(row=row_num, column=2, value=_safe(value))
            cell.number_format = fmt


def _write_wacc_scalars(ws, result: AssemblyResult) -> None:
    """Write WACC_001 scalar outputs to col B of Debt sheet (rows 22–30)."""
    wacc_out = result.outputs.get("WACC_001")

    for field_name in WACC_SCALAR_FIELDS:
        key = ("Debt", "WACC_001", field_name)
        if key not in ROW_MAP:
            continue
        row_num = ROW_MAP[key]

        label_cell = ws.cell(row=row_num, column=1, value=get_label(field_name))
        label_cell.font = _BOLD_FONT
        ws.cell(row=row_num, column=2, value=get_units(field_name))

        if wacc_out is None:
            continue
        value = _get_field_value(wacc_out, field_name)
        if value is not None:
            cell = ws.cell(row=row_num, column=2, value=_safe(value))
            cell.number_format = _num_format(field_name)


def _write_summary(ws, result: AssemblyResult) -> None:
    """Write the Summary sheet: project metadata + scalar KPIs."""
    ws.cell(row=1, column=1, value=result.project_name).font = Font(bold=True, size=14)

    ws.cell(row=3, column=1, value="Metric").font = _BOLD_FONT
    ws.cell(row=3, column=2, value="Value").font = _BOLD_FONT
    ws.cell(row=3, column=3, value="Units").font = _BOLD_FONT
    for c in range(1, 4):
        ws.cell(row=3, column=c).fill = _HEADER_FILL
        ws.cell(row=3, column=c).font = _WHITE_FONT

    # Build KPI rows
    kpis: list[tuple[str, Any, str]] = []  # (label, value, units)

    irr_out = result.outputs.get("IRR_001")
    if irr_out is not None:
        kpis.append(("Project IRR",            _safe(irr_out.project_irr),            "%"))
        kpis.append(("Equity IRR",             _safe(irr_out.equity_irr),             "%"))
        kpis.append(("Project NPV",            _safe(irr_out.project_npv),            "DKKk"))
        kpis.append(("Equity NPV",             _safe(irr_out.equity_npv),             "DKKk"))
        pb = irr_out.payback_period_months
        kpis.append(("Payback Period",         pb if pb >= 0 else "Never",            "months"))

    capex_out = result.outputs.get("CAPEX_001")
    if capex_out is not None:
        kpis.append(("Total CAPEX",            _safe(capex_out.cumulative_capex[-1]), "DKKk"))

    debt_out = result.outputs.get("DEBT_001")
    if debt_out is not None:
        kpis.append(("Min DSCR",               _safe(debt_out.min_dscr),             "x"))
        kpis.append(("Total Interest",         _safe(debt_out.total_interest),        "DKKk"))

    pl_out = result.outputs.get("PL_001")
    if pl_out is not None:
        kpis.append(("Lifetime Net Income",    _safe(sum(pl_out.net_income)),         "DKKk"))

    wacc_out = result.outputs.get("WACC_001")
    if wacc_out is not None:
        kpis.append(("WACC",                   _safe(wacc_out.wacc),                  "%"))
        kpis.append(("Blended CoE",            _safe(wacc_out.blended_cost_of_equity),"%"))

    _PCT_SUMMARY = {"Project IRR", "Equity IRR", "WACC", "Blended CoE"}
    _RATIO_SUMMARY = {"Min DSCR"}

    for i, (label, value, units) in enumerate(kpis):
        row = i + 4
        ws.cell(row=row, column=1, value=label)
        cell = ws.cell(row=row, column=2, value=value)
        ws.cell(row=row, column=3, value=units)
        if label in _PCT_SUMMARY and isinstance(value, float):
            cell.number_format = FMT_PCT
        elif label in _RATIO_SUMMARY and isinstance(value, float):
            cell.number_format = FMT_RATIO
        elif units == "DKKk" and isinstance(value, float):
            cell.number_format = FMT_DKKK


# ============================================================================
# PUBLIC API
# ============================================================================

def write_workbook(
    result: AssemblyResult,
    config: ProjectConfig,
    output_path: str,
) -> None:
    """
    Write result to a standard 5-sheet .xlsx workbook at output_path.

    Parameters
    ----------
    result      : AssemblyResult from engine.run()
    config      : ProjectConfig used to produce result (for project metadata)
    output_path : Destination file path (created or overwritten)
    """
    wb = openpyxl.Workbook()
    # Remove the default sheet
    wb.remove(wb.active)

    n  = result.periods
    sy = result.start_year
    sm = result.start_month

    for sheet_name in SHEETS:
        ws = wb.create_sheet(sheet_name)

        if sheet_name == "Summary":
            _write_summary(ws, result)
            ws.column_dimensions["A"].width = 28
            ws.column_dimensions["B"].width = 16
            ws.column_dimensions["C"].width = 10
            continue

        # Write standard header rows 1–4
        _write_header_rows(ws, result.project_name, n, sy, sm)

        # Write time-series data rows
        _write_time_series_rows(ws, result)

        # WACC scalars are col-B only on the Debt sheet
        if sheet_name == "Debt":
            _write_wacc_scalars(ws, result)

        # Column widths
        ws.column_dimensions["A"].width = 32
        ws.column_dimensions["B"].width = 10

    wb.save(output_path)
