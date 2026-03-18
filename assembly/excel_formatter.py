"""
assembly/excel_formatter.py

EE/F1F9 format standard — visual formatting layer for the Excel workbook.
Applied AFTER all data is written. Never changes cell values (except section
header labels written into empty rows).

Hybrid of two reverse-engineered reference models:
  EE Holsted Hybrid (PwC DK): fill-based structure colors
  F1F9 Renewables Template:   font-color data flow coding
"""

from __future__ import annotations

from openpyxl.styles import PatternFill, Font, Border, Side
from openpyxl.utils import get_column_letter

from assembly.cell_mapper import (
    ROW_MAP, COL_LABEL, COL_UNIT, COL_TOTAL, COL_PERIOD_0,
)

# ============================================================================
# COLOR CONSTANTS — EE FORMAT STANDARD
# ============================================================================

# Structure fills (EE Holsted Hybrid)
FILL_SECTION_HEADER = "28837D"  # dark teal  — sub-section group labels
FILL_COL_HEADER     = "44546A"  # slate blue — column header row (row 5)
FILL_PHASE_STRIPE   = "F8F8F8"  # near-white — Construction/Operations row
FILL_TOTAL_COL      = "F2F2F2"  # light grey — col J total/avg
FILL_COUNTER_FLOW   = "DDDDDD"  # mid grey   — counter-flow rows

# Input fills
FILL_INPUT          = "FFF2CC"  # pale yellow — editable cells

# Quality fills
FILL_CHECK_PASS     = "99FF66"  # green — checks passing
FILL_CHECK_FAIL     = "FF0000"  # red   — checks failing

# Font colors (F1F9 standard)
FONT_DEFAULT        = "000000"  # black  — standard calculation
FONT_INPUT          = "0020FF"  # blue   — input cell values
FONT_IMPORT         = "0000FF"  # blue   — row imported from another sheet
FONT_EXPORT         = "FF0000"  # red    — row exported to another sheet
FONT_WHITE          = "FFFFFF"  # white  — on dark fills

# Cover accent
FILL_COVER_HEADER   = "008080"  # teal   — project banner
FILL_COVER_KPI      = "EBF3FB"  # light blue — KPI block background
FILL_COVER_KEY_ROW  = "F2F2F2"  # light grey — color key legend rows


# ============================================================================
# ROW CLASSIFICATION SETS
# ============================================================================

# Red font — values used by other sheets
EXPORT_FIELDS = {
    "net_revenue", "total_ppa_revenue", "merchant_revenue", "goo_revenue",
    "total_opex", "total_capex_monthly", "cumulative_capex",
    "interest", "principal_repayment", "closing_balance", "dscr_annual",
    "tax_charge_accrued", "tax_paid", "tax_depreciation",
    "net_income", "cfo", "cfi", "cff", "net_cash_flow", "closing_cash",
    "total_assets", "total_liabilities_equity",
}

# Blue font — values pulled from other sheets
IMPORT_FIELDS = {
    "gross_revenue", "depreciation", "interest_expense",
    "capex_monthly", "debt_drawdown",
    "net_production_MWh", "discharge_volume",
}

# Thin top+bottom border — subtotals
SUBTOTAL_FIELDS = {
    "ebitda", "ebit", "ebt", "cfo", "cfi", "cff",
    "net_cash_flow", "total_assets", "total_liabilities_equity", "net_revenue",
}

# Bold + medium top border — totals
TOTAL_FIELDS = {
    "net_income", "closing_cash", "project_irr", "equity_irr", "cumulative_capex",
}

# Section header rows: {sheet_name: {row_number: label}}
# Row numbers chosen to sit in empty gaps between module groups in ROW_MAP
SECTION_HEADER_ROWS = {
    "Revenue":    {29: "BESS Revenue", 60: "Wind Revenue", 83: "PPA / CfD"},
    "Costs":      {19: "BESS OPEX", 26: "Capital Expenditure",
                   36: "Wind OPEX", 43: "BESS Repowering"},
    "Debt":       {21: "WACC", 32: "Shareholder Loan",
                   38: "VAT Facility", 45: "Sculpted Debt"},
    "FS_Monthly": {15: "Cash Flow", 33: "Balance Sheet"},
    "FS_Annual":  {15: "Cash Flow", 33: "Balance Sheet"},
}


# ============================================================================
# HELPERS
# ============================================================================

def _get_sheet_rows(sheet_name: str) -> list[tuple[int, str, str]]:
    """Return [(row_number, module_id, field_name)] for a given sheet."""
    logical = "Statements" if sheet_name in ("FS_Monthly", "FS_Annual") else sheet_name
    rows = []
    for (sht, mod, field), row in ROW_MAP.items():
        if sht == logical:
            rows.append((row, mod, field))
    return sorted(rows)


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def _font(hex_color: str = FONT_DEFAULT, bold: bool = False,
          size: int = 10) -> Font:
    return Font(color=hex_color, bold=bold, name="Calibri", size=size)


def _border_subtotal() -> Border:
    """Thin top and bottom border for subtotal rows."""
    thin = Side(border_style="thin")
    return Border(top=thin, bottom=thin)


def _border_total() -> Border:
    """Medium top border for total rows."""
    return Border(top=Side(border_style="medium"))


# ============================================================================
# FORMAT FUNCTIONS
# ============================================================================

def _format_header_rows(ws, n_cols: int) -> None:
    """Format row 5 (col headers) and row 3 (phase stripe)."""
    last_col = COL_PERIOD_0 + n_cols - 1

    # Row 5: col header — slate fill, white bold
    for col in range(1, last_col + 1):
        cell = ws.cell(row=5, column=col)
        cell.fill = _fill(FILL_COL_HEADER)
        cell.font = _font(FONT_WHITE, bold=True)

    # Row 3: phase stripe — near-white fill
    for col in range(COL_PERIOD_0, last_col + 1):
        ws.cell(row=3, column=col).fill = _fill(FILL_PHASE_STRIPE)


def _format_data_rows(ws, sheet_name: str, n_cols: int) -> None:
    """Apply font colors, borders, and col J fill to all data rows."""
    last_data_col = COL_PERIOD_0 + n_cols - 1
    rows = _get_sheet_rows(sheet_name)

    for row_num, mod_id, field in rows:
        # Col J: total column fill on every data row
        ws.cell(row=row_num, column=COL_TOTAL).fill = _fill(FILL_TOTAL_COL)

        # Font color for entire row E through last period col
        if field in EXPORT_FIELDS:
            font_color = FONT_EXPORT
        elif field in IMPORT_FIELDS:
            font_color = FONT_IMPORT
        else:
            font_color = FONT_DEFAULT

        for col in range(COL_LABEL, last_data_col + 1):
            cell = ws.cell(row=row_num, column=col)
            existing_bold = cell.font.bold if cell.font else False
            cell.font = _font(font_color, bold=existing_bold)

        # Subtotal border
        if field in SUBTOTAL_FIELDS:
            for col in range(COL_LABEL, last_data_col + 1):
                ws.cell(row=row_num, column=col).border = _border_subtotal()

        # Total border + bold label
        elif field in TOTAL_FIELDS:
            for col in range(COL_LABEL, last_data_col + 1):
                ws.cell(row=row_num, column=col).border = _border_total()
            label_cell = ws.cell(row=row_num, column=COL_LABEL)
            label_cell.font = _font(font_color, bold=True)


def _format_section_headers(ws, sheet_name: str) -> None:
    """Write section header labels into empty rows with teal fill."""
    headers = SECTION_HEADER_ROWS.get(sheet_name, {})
    for row_num, label in headers.items():
        # Only write into rows that are currently empty (safety check)
        existing = ws.cell(row=row_num, column=COL_LABEL).value
        if existing is not None:
            continue
        for col in range(COL_LABEL, COL_UNIT + 1):  # cols E, F, G
            cell = ws.cell(row=row_num, column=col)
            cell.fill = _fill(FILL_SECTION_HEADER)
            cell.font = _font(FONT_WHITE, bold=True)
        ws.cell(row=row_num, column=COL_LABEL).value = label


# ============================================================================
# PUBLIC API
# ============================================================================

def apply_formatting(wb, result) -> None:
    """
    Apply EE/F1F9 format standard to a fully written workbook.
    Called AFTER all data is written.

    Order per sheet:
      1. Col header row 5: FILL_COL_HEADER, white bold font
      2. Phase stripe row 3: FILL_PHASE_STRIPE
      3. Section header rows: FILL_SECTION_HEADER, white bold, cols E-G
      4. Col J (total) fill: FILL_TOTAL_COL on every data row
      5. Data row font colors: EXPORT=red, IMPORT=blue, default=black
      6. Subtotal rows: thin top+bottom border
      7. Total rows: medium top border + bold label
    """
    CALC_SHEETS = ["Revenue", "Costs", "Debt", "FS_Monthly", "FS_Annual"]
    n = result.periods

    for sheet_name in CALC_SHEETS:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]

        if sheet_name == "FS_Annual":
            years = set()
            for p in range(n):
                years.add(result.start_year + ((result.start_month - 1) + p) // 12)
            n_cols = len(years)
        else:
            n_cols = n

        _format_header_rows(ws, n_cols)
        _format_data_rows(ws, sheet_name, n_cols)
        _format_section_headers(ws, sheet_name)
