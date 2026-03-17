"""Tests for assembly/parser.py — vendor model parser functions."""

import pytest
from assembly.parser import (
    parse_xlsx,
    ROW_MAP_NTD,
    CURVE_BLOCKS,
    _find_sheet,
    _parse_input_ntd,
    _parse_input_m,
    _parse_input_a,
    _parse_global,
    parse_vendor_model,
)


# ---------------------------------------------------------------------------
# ROW_MAP_NTD structure tests (no Excel file needed)
# ---------------------------------------------------------------------------

def test_row_map_ntd_has_entries():
    assert len(ROW_MAP_NTD) > 20


def test_row_map_ntd_general_fields():
    assert 13 in ROW_MAP_NTD
    assert ROW_MAP_NTD[13] == ("scenario_name", str)


def test_row_map_ntd_capacity_fields():
    assert 32 in ROW_MAP_NTD
    assert ROW_MAP_NTD[32][0] == "res_total_capacity_mw"


def test_row_map_ntd_production_fields():
    assert 66 in ROW_MAP_NTD
    assert ROW_MAP_NTD[66][0] == "production_p50_mwh"


def test_row_map_ntd_ppa_fields():
    assert 101 in ROW_MAP_NTD
    assert ROW_MAP_NTD[101][0] == "ppa_active"


def test_row_map_ntd_opex_tax_fields():
    assert 484 in ROW_MAP_NTD
    assert ROW_MAP_NTD[484][0] == "cit_rate"


def test_row_map_ntd_financing_fields():
    assert 711 in ROW_MAP_NTD
    assert ROW_MAP_NTD[711][0] == "shl_pct"


# ---------------------------------------------------------------------------
# CURVE_BLOCKS structure tests
# ---------------------------------------------------------------------------

def test_curve_blocks_has_entries():
    assert len(CURVE_BLOCKS) >= 5


def test_curve_blocks_capture():
    block = CURVE_BLOCKS[0]
    assert block[2] == "capture"
    assert block[3] == "EUR/MWh"


def test_curve_blocks_bess():
    block = CURVE_BLOCKS[1]
    assert block[2] == "bess_revenue"


def test_curve_blocks_interest():
    interest_blocks = [b for b in CURVE_BLOCKS if b[2] == "interest"]
    assert len(interest_blocks) >= 1


# ---------------------------------------------------------------------------
# parse_vendor_model — file not found
# ---------------------------------------------------------------------------

def test_parse_vendor_model_file_not_found():
    with pytest.raises(FileNotFoundError):
        parse_vendor_model("nonexistent_file.xlsx")


def test_parse_vendor_model_xlsb_rejected():
    # Create a dummy .xlsb path (doesn't need to exist for this check)
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), "test.xlsb")
    # Create empty file so FileNotFoundError doesn't trigger first
    with open(path, "w") as f:
        f.write("")
    try:
        with pytest.raises(ValueError, match="xlsb"):
            parse_vendor_model(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# _find_sheet — mock workbook
# ---------------------------------------------------------------------------

class MockWorkbook:
    def __init__(self, names):
        self.sheetnames = names
        self._sheets = {n: f"sheet_{n}" for n in names}
    def __getitem__(self, key):
        return self._sheets[key]


def test_find_sheet_by_prefix():
    wb = MockWorkbook(["1.1 Input_NTD", "1.2 Input_M", "Summary"])
    assert _find_sheet(wb, "1.1") == "sheet_1.1 Input_NTD"


def test_find_sheet_missing():
    wb = MockWorkbook(["Summary"])
    assert _find_sheet(wb, "1.1") is None
