"""Integration tests for assembly/excel_writer.py — openpyxl workbook writer."""

import os
import math
import pytest
import openpyxl

from assembly.engine import (
    ProjectConfig,
    TimelineConfig,
    TaxConfig,
    StatementConfig,
    run,
)
from assembly.excel_writer import write_workbook, SHEETS, _period_to_label
from assembly.cell_mapper import get_row, period_col, col_letter
import modules.revenue.REV_001 as REV_001
import modules.costs.OPEX_001 as OPEX_001
import modules.capex.CAPEX_001 as CAPEX_001
import modules.debt.DEBT_001 as DEBT_001


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cr(annual=100.0):
    return OPEX_001.ComponentRate(annual_DKKk=annual)


def _zero_cr():
    return _cr(0.0)


@pytest.fixture
def minimal_result(tmp_path):
    n = 24
    config = ProjectConfig(
        project_name="Writer Test",
        timeline=TimelineConfig(periods=n, start_year=2026, start_month=1),
        rev_pv=REV_001.Inputs(
            periods=n, start_year=2026, start_month=1,
            net_production_MWh=[500.0] * n,
            wholesale_price_DKK_per_MWh=[400.0] * n,
            capture_price_DKK_per_MWh=[380.0] * n,
            goo_price_DKK_per_MWh=[5.0] * n,
        ),
        opex_pv=OPEX_001.Inputs(
            periods=n, start_year=2026, start_month=1,
            capacity_mwp=10.0,
            om=_cr(500.0), commercial_management=_zero_cr(),
            inverter_replacement=_zero_cr(), insurance=_cr(100.0),
            other=_zero_cr(),
        ),
        capex=CAPEX_001.Inputs(
            periods=n,
            construction_start_period=0, construction_periods=12,
            drawdown_profile=[1.0 / 12] * 12,
            epc_DKKk=40_000.0, grid_connection_DKKk=5_000.0,
            development_DKKk=2_000.0, land_DKKk=0.0,
            contingency_pct=0.05, other_DKKk=0.0,
        ),
        debt=DEBT_001.Inputs(
            periods=n, start_year=2026, start_month=1,
            facility=35_000.0, all_in_rate=0.05,
            repayment_type="annuity", tenor_months=12,
            drawdowns=[35_000.0 / 12] * 12 + [0.0] * 12,
        ),
        tax=TaxConfig(country="DK"),
        statements=StatementConfig(
            opening_cash_DKKk=1_000.0,
            opening_contributed_equity_DKKk=15_000.0,
        ),
    )
    result = run(config)
    return result, config


@pytest.fixture
def written_wb(minimal_result, tmp_path):
    result, config = minimal_result
    out_path = str(tmp_path / "test_out.xlsx")
    write_workbook(result, config, out_path)
    return openpyxl.load_workbook(out_path), result, config, out_path


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

def test_creates_file(minimal_result, tmp_path):
    result, config = minimal_result
    out_path = str(tmp_path / "output.xlsx")
    write_workbook(result, config, out_path)
    assert os.path.exists(out_path)


def test_file_is_valid_xlsx(written_wb):
    wb, *_ = written_wb
    assert wb is not None


# ---------------------------------------------------------------------------
# Sheets present
# ---------------------------------------------------------------------------

def test_all_sheets_present(written_wb):
    wb, *_ = written_wb
    assert set(wb.sheetnames) == set(SHEETS)


def test_sheet_order(written_wb):
    wb, *_ = written_wb
    assert wb.sheetnames == SHEETS


# ---------------------------------------------------------------------------
# Header rows
# ---------------------------------------------------------------------------

def test_header_row1_contains_project_name(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Revenue"]
    assert ws.cell(row=1, column=1).value == result.project_name


def test_period_row3_starts_at_zero(written_wb):
    wb, *_ = written_wb
    ws = wb["Revenue"]
    assert ws.cell(row=3, column=3).value == 0   # col C = period 0


def test_period_row3_increments(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Revenue"]
    assert ws.cell(row=3, column=4).value == 1   # col D = period 1
    assert ws.cell(row=3, column=5).value == 2


def test_date_row4_jan_2026(written_wb):
    wb, *_ = written_wb
    ws = wb["Revenue"]
    assert ws.cell(row=4, column=3).value == "Jan-2026"


def test_date_row4_feb_2026(written_wb):
    wb, *_ = written_wb
    ws = wb["Revenue"]
    assert ws.cell(row=4, column=4).value == "Feb-2026"


# ---------------------------------------------------------------------------
# Data rows — Revenue sheet
# ---------------------------------------------------------------------------

def test_rev001_net_revenue_written(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Revenue"]
    row = get_row("REV_001", "net_revenue")
    rev_out = result.outputs["REV_001"]
    expected = rev_out.net_revenue[0]
    actual = ws.cell(row=row, column=period_col(0)).value
    assert actual == pytest.approx(expected, rel=1e-6)


def test_rev001_label_in_col_a(written_wb):
    wb, *_ = written_wb
    ws = wb["Revenue"]
    row = get_row("REV_001", "net_revenue")
    label = ws.cell(row=row, column=1).value
    assert label is not None and "revenue" in label.lower()


def test_rev001_units_in_col_b(written_wb):
    wb, *_ = written_wb
    ws = wb["Revenue"]
    row = get_row("REV_001", "net_revenue")
    units = ws.cell(row=row, column=2).value
    assert units == "DKKk"


# ---------------------------------------------------------------------------
# Data rows — Costs sheet
# ---------------------------------------------------------------------------

def test_opex_total_written(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Costs"]
    row = get_row("OPEX_001", "total_opex")
    opex_out = result.outputs["OPEX_001"]
    expected = opex_out.total_opex[0]
    actual = ws.cell(row=row, column=period_col(0)).value
    assert actual == pytest.approx(expected, rel=1e-6)


def test_capex_cumulative_written(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Costs"]
    row = get_row("CAPEX_001", "cumulative_capex")
    capex_out = result.outputs["CAPEX_001"]
    for p in range(3):
        expected = capex_out.cumulative_capex[p]
        actual = ws.cell(row=row, column=period_col(p)).value
        assert actual == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Data rows — Statements sheet
# ---------------------------------------------------------------------------

def test_pl_net_income_written(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Statements"]
    row = get_row("PL_001", "net_income")
    pl_out = result.outputs["PL_001"]
    expected = pl_out.net_income[0]
    actual = ws.cell(row=row, column=period_col(0)).value
    assert actual == pytest.approx(expected, rel=1e-6)


def test_irr_cumulative_pfcf_written(written_wb):
    wb, result, *_ = written_wb
    if "IRR_001" not in result.outputs:
        pytest.skip("IRR_001 not in outputs")
    ws = wb["Statements"]
    row = get_row("IRR_001", "cumulative_pfcf")
    irr_out = result.outputs["IRR_001"]
    expected = irr_out.cumulative_pfcf[0]
    actual = ws.cell(row=row, column=period_col(0)).value
    assert actual == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Summary sheet
# ---------------------------------------------------------------------------

def test_summary_sheet_has_project_name(written_wb):
    wb, result, *_ = written_wb
    ws = wb["Summary"]
    assert ws.cell(row=1, column=1).value == result.project_name


def test_summary_has_irr_entry(written_wb):
    wb, result, *_ = written_wb
    if "IRR_001" not in result.outputs:
        pytest.skip("IRR_001 not in outputs")
    ws = wb["Summary"]
    labels = [ws.cell(row=r, column=1).value for r in range(1, 30)]
    assert any(l and "irr" in str(l).lower() for l in labels)


def test_summary_has_npv_entry(written_wb):
    wb, result, *_ = written_wb
    if "IRR_001" not in result.outputs:
        pytest.skip("IRR_001 not in outputs")
    ws = wb["Summary"]
    labels = [ws.cell(row=r, column=1).value for r in range(1, 30)]
    assert any(l and "npv" in str(l).lower() for l in labels)


# ---------------------------------------------------------------------------
# Disabled module rows are empty
# ---------------------------------------------------------------------------

def test_disabled_module_rows_have_no_data(tmp_path):
    """REV_002 disabled → its rows in Revenue sheet have no numeric values."""
    n = 12
    config = ProjectConfig(
        project_name="PV Only",
        timeline=TimelineConfig(periods=n, start_year=2026, start_month=1),
        rev_pv=REV_001.Inputs(
            periods=n, start_year=2026, start_month=1,
            net_production_MWh=[500.0] * n,
            wholesale_price_DKK_per_MWh=[400.0] * n,
            capture_price_DKK_per_MWh=[380.0] * n,
            goo_price_DKK_per_MWh=[5.0] * n,
        ),
        # rev_bess = None → disabled
    )
    result = run(config)
    out_path = str(tmp_path / "pv_only.xlsx")
    write_workbook(result, config, out_path)

    wb = openpyxl.load_workbook(out_path)
    ws = wb["Revenue"]
    row = get_row("REV_002", "net_revenue")
    # Period columns should be None (no data written)
    for p in range(n):
        val = ws.cell(row=row, column=period_col(p)).value
        assert val is None, f"Expected None at REV_002 row period {p}, got {val}"


# ---------------------------------------------------------------------------
# _period_to_label
# ---------------------------------------------------------------------------

def test_period_to_label_jan_start():
    assert _period_to_label(2026, 1, 0) == "Jan-2026"


def test_period_to_label_rolls_over_year():
    assert _period_to_label(2026, 1, 12) == "Jan-2027"


def test_period_to_label_mid_year():
    assert _period_to_label(2026, 6, 6) == "Dec-2026"


def test_period_to_label_dec_rolls_to_jan():
    assert _period_to_label(2026, 12, 1) == "Jan-2027"
