"""
assembly/parser.py

Parses operator-uploaded .xlsx files into a flat dict of assumption values.

Two strategies are tried in order:
    1. Named ranges  — workbook-level defined names; each maps to a single cell.
    2. Assumptions sheet — sheet named "Assumptions" with col A = name, col B = value.

Returns a flat dict {str: any} with string keys and numeric/string values.

Limitations:
    .xlsb files are NOT supported (openpyxl cannot read them).
    A ValueError is raised with instructions to save as .xlsx first.
"""

from __future__ import annotations

import os


def parse_xlsx(file_path: str) -> dict:
    """
    Parse assumption values from an Excel file.

    Parameters
    ----------
    file_path : Path to the .xlsx file.

    Returns
    -------
    dict
        Flat dict of {name: value} pairs. Keys are stripped of whitespace.
        Values are whatever openpyxl reads from the cell (float, int, str, bool, None).

    Raises
    ------
    FileNotFoundError
        If file_path does not exist.
    ValueError
        If the file extension is .xlsb (binary format not supported).
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path!r}")

    _, ext = os.path.splitext(file_path)
    if ext.lower() == ".xlsb":
        raise ValueError(
            f"Binary Excel files (.xlsb) are not supported. "
            f"Please open {os.path.basename(file_path)!r} in Excel, "
            f"choose 'Save As', and select 'Excel Workbook (.xlsx)'."
        )

    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)

    # Strategy 1: named ranges
    result = _read_named_ranges(wb)

    # Strategy 2: "Assumptions" sheet (merge, Strategy 2 wins on conflict)
    assumptions_data = _read_assumptions_sheet(wb)
    result.update(assumptions_data)

    return result


def _read_named_ranges(wb) -> dict:
    """
    Extract workbook-level defined names that resolve to a single cell.

    Only single-cell destinations are extracted (multi-cell ranges are skipped).
    """
    data: dict = {}

    for name in wb.defined_names:
        # openpyxl 3.1+: iterating yields string keys; look up the DefinedName object
        defined_name = wb.defined_names[name]
        # Each DefinedName may have multiple destinations (sheet, coord pairs)
        destinations = list(defined_name.destinations)
        if len(destinations) != 1:
            continue  # skip multi-range or sheet-level names

        sheet_title, coord = destinations[0]
        ws = wb[sheet_title] if sheet_title in wb.sheetnames else None
        if ws is None:
            continue

        # coord may be an absolute reference like "$B$5"
        coord_clean = coord.replace("$", "")
        try:
            cell = ws[coord_clean]
        except (KeyError, TypeError):
            continue

        if hasattr(cell, "value"):
            key = name.strip()
            data[key] = cell.value

    return data


def _read_assumptions_sheet(wb) -> dict:
    """
    Read from a sheet named 'Assumptions' (case-insensitive).

    Expects:
        Column A = assumption name (str)
        Column B = assumption value (numeric or str)

    Blank rows and rows where col A is not a string are skipped.
    """
    # Find the sheet case-insensitively
    target_ws = None
    for name in wb.sheetnames:
        if name.strip().lower() == "assumptions":
            target_ws = wb[name]
            break

    if target_ws is None:
        return {}

    data: dict = {}
    for row in target_ws.iter_rows(min_row=1, values_only=True):
        if len(row) < 2:
            continue
        name_cell, value_cell = row[0], row[1]
        if not isinstance(name_cell, str):
            continue
        key = name_cell.strip()
        if not key:
            continue
        data[key] = value_cell

    return data
