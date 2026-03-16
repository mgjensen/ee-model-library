"""Tests for assembly/cell_mapper.py — row/column layout helpers."""

import pytest
from assembly.cell_mapper import (
    MODULE_SHEET,
    ROW_MAP,
    col_letter,
    period_col,
    get_cell,
    get_row,
    get_sheet,
    get_label,
    get_units,
    FIELD_UNITS_DEFAULT,
)


# ---------------------------------------------------------------------------
# col_letter
# ---------------------------------------------------------------------------

def test_col_letter_single_a():
    assert col_letter(1) == "A"


def test_col_letter_single_z():
    assert col_letter(26) == "Z"


def test_col_letter_double_aa():
    assert col_letter(27) == "AA"


def test_col_letter_double_az():
    assert col_letter(52) == "AZ"


def test_col_letter_double_ba():
    assert col_letter(53) == "BA"


def test_col_letter_triple_aaa():
    assert col_letter(703) == "AAA"


# ---------------------------------------------------------------------------
# period_col
# ---------------------------------------------------------------------------

def test_period_col_zero_is_col_c():
    assert period_col(0) == 3   # col C


def test_period_col_one_is_col_d():
    assert period_col(1) == 4   # col D


def test_period_col_large():
    assert period_col(471) == 474


# ---------------------------------------------------------------------------
# get_cell
# ---------------------------------------------------------------------------

def test_get_cell_format():
    assert get_cell("Revenue", 6, 3) == "Revenue!C6"


def test_get_cell_period_zero_net_production():
    # REV_001.net_production_MWh → row 6; period 0 → col C
    row = get_row("REV_001", "net_production_MWh")
    col = period_col(0)
    assert get_cell("Revenue", row, col) == "Revenue!C6"


# ---------------------------------------------------------------------------
# get_row — known spot checks
# ---------------------------------------------------------------------------

def test_get_row_rev_001_net_revenue():
    assert get_row("REV_001", "net_revenue") == 27


def test_get_row_rev_002_net_revenue():
    assert get_row("REV_002", "net_revenue") == 57


def test_get_row_opex_001_total_opex():
    assert get_row("OPEX_001", "total_opex") == 17


def test_get_row_opex_002_total_opex():
    assert get_row("OPEX_002", "total_opex") == 24


def test_get_row_capex_001_cumulative():
    assert get_row("CAPEX_001", "cumulative_capex") == 34


def test_get_row_debt_001_closing_balance():
    assert get_row("DEBT_001", "closing_balance") == 10


def test_get_row_tax_001_tax_paid():
    assert get_row("TAX_001", "tax_paid") == 19


def test_get_row_pl_001_net_income():
    assert get_row("PL_001", "net_income") == 14


def test_get_row_cf_001_closing_cash():
    assert get_row("CF_001", "closing_cash") == 32


def test_get_row_bs_001_imbalance():
    assert get_row("BS_001", "imbalance") == 45


def test_get_row_irr_001_cumulative_pfcf():
    assert get_row("IRR_001", "cumulative_pfcf") == 47


def test_get_row_wacc_001_wacc():
    assert get_row("WACC_001", "wacc") == 22


# ---------------------------------------------------------------------------
# get_row — error cases
# ---------------------------------------------------------------------------

def test_get_row_unknown_module_raises():
    with pytest.raises(KeyError, match="UNKNOWN"):
        get_row("UNKNOWN", "some_field")


def test_get_row_unknown_field_raises():
    with pytest.raises(KeyError, match="not_a_field"):
        get_row("REV_001", "not_a_field")


# ---------------------------------------------------------------------------
# get_sheet
# ---------------------------------------------------------------------------

def test_get_sheet_rev_001():
    assert get_sheet("REV_001") == "Revenue"


def test_get_sheet_wacc_001():
    assert get_sheet("WACC_001") == "Debt"


def test_get_sheet_irr_001():
    assert get_sheet("IRR_001") == "Statements"


def test_get_sheet_unknown_raises():
    with pytest.raises(KeyError):
        get_sheet("NONEXISTENT")


# ---------------------------------------------------------------------------
# No row collisions per sheet
# ---------------------------------------------------------------------------

def test_no_row_collisions_revenue_sheet():
    rows = [row for (sh, _, _), row in ROW_MAP.items() if sh == "Revenue"]
    assert len(rows) == len(set(rows)), "Duplicate row numbers in Revenue sheet"


def test_no_row_collisions_costs_sheet():
    rows = [row for (sh, _, _), row in ROW_MAP.items() if sh == "Costs"]
    assert len(rows) == len(set(rows)), "Duplicate row numbers in Costs sheet"


def test_no_row_collisions_debt_sheet():
    rows = [row for (sh, _, _), row in ROW_MAP.items() if sh == "Debt"]
    assert len(rows) == len(set(rows)), "Duplicate row numbers in Debt sheet"


def test_no_row_collisions_statements_sheet():
    rows = [row for (sh, _, _), row in ROW_MAP.items() if sh == "Statements"]
    assert len(rows) == len(set(rows)), "Duplicate row numbers in Statements sheet"


# ---------------------------------------------------------------------------
# get_label / get_units
# ---------------------------------------------------------------------------

def test_get_label_known():
    assert get_label("net_revenue") == "Net Revenue"


def test_get_label_unknown_returns_field_name():
    assert get_label("completely_unknown_field") == "completely_unknown_field"


def test_get_units_mwh_field():
    assert get_units("net_production_MWh") == "MWh"


def test_get_units_pct_field():
    assert get_units("wacc") == "%"


def test_get_units_default():
    assert get_units("net_revenue") == FIELD_UNITS_DEFAULT


# ---------------------------------------------------------------------------
# Row map completeness: all ROW_MAP keys have valid module_id in MODULE_SHEET
# ---------------------------------------------------------------------------

def test_row_map_module_ids_in_module_sheet():
    for (sheet, module_id, field), row in ROW_MAP.items():
        assert module_id in MODULE_SHEET, f"{module_id} missing from MODULE_SHEET"
