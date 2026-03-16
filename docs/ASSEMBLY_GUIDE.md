# Assembly Guide

How the assembly layer orchestrates calculation modules and produces the `.xlsx` output.

---

## Overview

The assembly layer has four components:

| File | Purpose |
|------|---------|
| `engine.py` | Runs modules in dependency order; wires outputs into downstream inputs |
| `cell_mapper.py` | Maps (module_id, field_name) → row/column in the 5-sheet workbook |
| `excel_writer.py` | Writes an `AssemblyResult` to a `.xlsx` file using openpyxl |
| `parser.py` | Reads operator-uploaded `.xlsx` files into a flat assumption dict |

---

## Engine

### ProjectConfig

All module configurations are fields on `ProjectConfig`. Set a field to `None` to disable the module.

```python
config = ProjectConfig(
    project_name="Viuf PV+BESS",
    market="DK",
    technology="PV",
    timeline=TimelineConfig(periods=475, start_year=2026, start_month=1),

    # Revenue modules
    rev_pv=REV_001.Inputs(...),    # None = disabled
    rev_bess=REV_002.Inputs(...),
    rev_wind=REV_003.Inputs(...),

    # Cost modules
    opex_pv=OPEX_001.Inputs(...),
    opex_bess=OPEX_002.Inputs(...),
    opex_wind=OPEX_003.Inputs(...),

    # Capital & financing
    capex=CAPEX_001.Inputs(...),
    debt=DEBT_001.Inputs(...),

    # Wired modules (engine constructs Inputs from upstream outputs)
    tax=TaxConfig(country="DK"),
    statements=StatementConfig(
        opening_cash_DKKk=5_000.0,
        opening_contributed_equity_DKKk=225_000.0,
    ),
)
```

### Timeline injection

`engine.run()` calls `_inject_timeline(inp, tl)` before every module. This overrides `periods`, `start_year`, and `start_month` from the module's own config with the project-level `TimelineConfig`. You never need to set these fields in sub-configs — they are ignored.

### Execution order

```
Step 1:  WACC_001   — scalar cost of capital (no dependencies)
Step 2:  REV_001    — PV revenue
Step 3:  REV_002    — BESS revenue
Step 4:  REV_003    — Wind revenue
Step 5:  OPEX_001   — PV OPEX
Step 6:  OPEX_002   — BESS OPEX
Step 7:  OPEX_003   — Wind OPEX
Step 8:  CAPEX_001  — capital expenditure
Step 9:  DEBT_001   — debt schedule
Step 10: TAX_001    — WIRED: ebitda from (rev.net_revenue - opex.total_opex),
                              interest from debt.interest,
                              capex_by_bucket from capex.total_capex_monthly
Step 11: PL_001     — WIRED: gross_revenue, total_opex, depreciation, interest, tax_charge
Step 12: CF_001     — WIRED: net_income, depreciation, capex, debt drawdown/repayment
Step 13: BS_001     — WIRED: capex, depreciation, closing_cash, debt_balance, net_income
Step 14: IRR_001    — WIRED: pfcf = CFO+CFI, ecf = net_cash_flow; rates from WACC or override
```

### AssemblyResult

`run()` returns an `AssemblyResult`:

```python
result = run(config)

result.project_name        # str
result.periods             # int
result.start_year          # int
result.start_month         # int
result.outputs             # dict[str, Any] — keyed by module_id
result.warnings            # list[str]

# Access individual module output
rev_out = result.get("REV_001")   # REV_001.Outputs or None
irr_out = result.get("IRR_001")   # IRR_001.Outputs or None
```

---

## Excel Writer

```python
from assembly.excel_writer import write_workbook

write_workbook(result, config, "output/Viuf.xlsx")
```

Produces a 5-sheet workbook:

| Sheet | Content |
|-------|---------|
| Revenue | REV_001, REV_002, REV_003 monthly time-series |
| Costs | OPEX_001, OPEX_002, OPEX_003, CAPEX_001 monthly time-series |
| Debt | DEBT_001, TAX_001 monthly time-series; WACC_001 scalars in col B |
| Statements | PL_001, CF_001, BS_001, IRR_001 monthly time-series |
| Summary | Scalar KPIs: IRR, NPV, payback, total CAPEX, min DSCR, lifetime net income |

Row layout:
- Row 1: project name / sheet title
- Row 2: blank
- Row 3: period indices (0, 1, 2, …) in cols C+
- Row 4: date labels (Jan-2026, Feb-2026, …) in cols C+
- Row 5+: data rows per `ROW_MAP` in `cell_mapper.py`

Column layout:
- Col A: field label
- Col B: units string (or scalar value for WACC_001 fields)
- Col C+: monthly time-series (col C = period 0)

---

## Parser

Reads operator-uploaded `.xlsx` files using two strategies:

1. **Named ranges** — workbook-level defined names pointing to single cells
2. **Assumptions sheet** — sheet named "Assumptions" with col A = name, col B = value

Strategy 2 is applied after strategy 1 and wins on conflict.

```python
from assembly.parser import parse_xlsx

data = parse_xlsx("operator_model.xlsx")
# Returns: {"wacc": 0.07, "facility_DKKk": 450000.0, ...}

# .xlsb files raise ValueError with save-as instructions
# Missing files raise FileNotFoundError
```

---

## Cell Mapper

```python
from assembly.cell_mapper import get_row, get_sheet, get_cell, period_col, col_letter

get_row("REV_001", "net_revenue")          # → 27
get_sheet("CAPEX_001")                     # → "Costs"
get_cell("Revenue", 27, period_col(0))     # → "Revenue!C27"
col_letter(27)                             # → "AA"
```
