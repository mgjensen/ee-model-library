# CLAUDE.md

Python library that assembles production-ready Excel financial models for renewable energy projects (PV, BESS, Wind). Calculation modules → assembly engine → `.xlsx` output.

**Owner:** Martin Graa Jennum, European Energy A/S
**Stack:** Python 3.11+, Pydantic v2, openpyxl, pytest, FastAPI (stubbed)
**Currency:** DKKk throughout, except prices (DKK/MWh, EUR/MWh)

## Architecture state: v1.x → v2.0

The codebase is v1.x with per-type modules. A v2.0 generic architecture is planned.

**CURRENT (v1.x):** 8 separate debt modules, 3 OPEX modules, 3 revenue modules. Module IDs use `_NNN` suffix (`DEBT_001`, `REV_001`). Files in `modules/debt/DEBT_001.py`, etc.
**TARGET (v2.0):** 1 generic DEBT module, 1 generic OPEX module. Revenue and tax stay separate.

**Rule:** Follow v1.x naming when editing existing code. Use v2.0 patterns only when Martin says "migrate" or "build generic." When in doubt, check imports in `engine.py`.

## Commands

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                             # ALL tests — run before every commit
python -m pytest tests/test_debt/test_DEBT_001.py -v   # specific module
python -m pytest tests/ -k "sculpted" -v               # by keyword
uvicorn api.main:app --reload                          # API at localhost:8000/docs
```

## Strict rules

1. **Pure Python only** in modules. No numpy, no pandas. Use `math` from stdlib.
2. **All time-series outputs: length == `inputs.periods`**. Period 0 = first month of project.
3. **Pydantic v2**: `Field(...)` for required. `@model_validator(mode="after")` for array checks. Always `return self`.
4. **Never break existing tests.** Full suite before every commit.
5. **Never modify existing Outputs in a breaking way.** Add fields, don't rename or remove.
6. **Modules never import other modules.** All inter-module data flows through `engine.py`.
7. **Sequential module numbering**: REV_004 = new module, NOT REV_001 v2.
8. **Timeline injection**: engine overrides `periods/start_year/start_month` from `TimelineConfig`.
9. **Fix at source.** Never patch Excel output — fix the upstream module.
10. **Date stamps are mandatory.** Every module docstring must have `CREATED:` and `MODIFIED:` lines. Every registry entry must have `"created"` and `"modified"` fields. When bumping VERSION, always update MODIFIED to today's date (YYYY-MM-DD). Format: ISO 8601 date only, no time.

## Project structure

```
ee-model-library/
  api/main.py                     FastAPI
  assembly/
    engine.py                     Orchestrates modules in dependency order
    cell_mapper.py                (module, field) → Excel row/col + column constants
    excel_writer.py               openpyxl → 7-sheet .xlsx workbook
    excel_formatter.py            EE/F1F9 format standard (colors, borders, grouping, print)
    cover_writer.py               Cover sheet with perspective-aware KPI blocks
    parser.py                     Reads operator .xlsx
    scenario_runner.py            Multi-scenario + portfolio aggregation
    scenario_engine.py            Sensitivity + tornado charts
  modules/
    core/WACC_001.py
    market/PRICE_CURVES_001.py
    revenue/REV_001.py REV_002.py REV_003.py PPA_CFD_001.py
    costs/OPEX_001.py OPEX_002.py OPEX_003.py
    capex/CAPEX_001.py BESS_REPOW_001.py
    debt/DEBT_001.py DEBT_SCULPT_001.py DEBT_LINEAR_001.py
         CONSTR_FINANCE_001.py SHL_001.py VAT_FACILITY_001.py
         DEBT_REFI_001.py REPOW_DEBT_001.py DSRA_001.py
    tax/TAX_001.py TAX_DE_001.py
    statements/PL_001.py CF_001.py BS_001.py IRR_001.py
               WORKING_CAPITAL_001.py SOURCES_USES_001.py
               VALUATION_001.py BREAKEVEN_001.py
    checks/MODEL_CHECKS_001.py
    reporting/DASHBOARD_001.py
  registry/
    module_registry.json          Single source of truth for modules
    assumption_db/DK.json DE.json AU.json
  tests/
  scripts/smoke_test_format.py    Full-model smoke test for Excel output
  docs/
    CLAUDE_MODULES.md             Full module inventory + engine wiring
    CLAUDE_SCHEMAS.md             Full schemas + Excel formatting specs
  contributions/TEMPLATE.md
```

## Excel output architecture (format-layer)

### 7-sheet workbook
| Sheet | Contents |
|---|---|
| Cover | Project banner, view selector (Bank/IC/Audit), KPI block, color key |
| Revenue | REV_001, REV_002, REV_003, PPA_CFD_001, PRICE_CURVES_001 |
| Costs | OPEX_001–003, CAPEX_001, BESS_REPOW_001, DECOM_PROVISION_001, IMBALANCE_FEE_001 |
| Debt | DEBT_*, SHL_001, VAT_FACILITY_001, DSRA_001, MRA_001, TAX_*, WACC_001 scalars |
| FS_Monthly | PL_001, CF_001, BS_001, IRR_001, WORKING_CAPITAL_001, SOURCES_USES_001, etc. |
| FS_Annual | Same as FS_Monthly — annual aggregation (flows=SUM, stocks=closing) |
| Summary | Project metadata + scalar KPIs (IRR, NPV, DSCR, WACC) |

### Column layout (EE Standard — matches PwC/EY reference models)
```
A-D (1-4)   narrow indent spacers (width 1.3)
E   (5)     row description / label (width 40.5)        COL_LABEL
F   (6)     constant / assumption value (width 12.5)     COL_CONSTANT
G   (7)     unit (width 14.5)                            COL_UNIT
H   (8)     notes (hidden, outline level 1)              COL_NOTES
I   (9)     source (hidden, outline level 1)             COL_SOURCE
J   (10)    total / lifetime average (width 15.5)        COL_TOTAL
K   (11)    spacer (width 2.5)                           COL_SPACER
L+  (12+)   monthly time series, period 0 = col L        COL_PERIOD_0
```

### Row layout
```
Row 1:   sheet name (bold)
Row 2:   period end dates (monthly: DD-MMM-YY, annual: year integers)
Row 3:   phase labels ("Construction" / "Operations")
Row 4:   calendar year integers
Row 5:   column headers (Description, Constant, Unit, Total/avg., period counters)
Row 6:   blank spacer
Row 7+:  data rows per ROW_MAP in cell_mapper.py
```

Freeze pane: `L7` on all calculation sheets (locks header rows + label columns).

### Format standard (excel_formatter.py)
- **Section headers**: teal `28837D` fill, white bold — in SECTION_HEADER_ROWS gaps
- **Sub-section labels**: grey `A6A6A6` fill, white bold — in SUBSECTION_LABELS gaps
- **Col headers (row 5)**: slate `44546A`, white bold
- **Export rows** (net_revenue, cfo, etc.): red font `FF0000`
- **Import rows** (gross_revenue, depreciation, etc.): blue font `0000FF`
- **Subtotals** (ebitda, ebit, etc.): thin top+bottom border
- **Totals** (net_income, closing_cash): medium top border, bold
- **Col J**: light grey `F2F2F2` fill on all data rows
- **Row grouping**: detail rows `outline_level=1`, hidden by default (min 4 rows per section)
- **Row heights**: banner=22pt, col_header=20pt, section=18pt, data=15pt, spacer=5pt
- **Print**: landscape A3, fit to 1 page wide, repeat rows 1:6 + cols A:K
- **Tab colors**: Cover=teal, calc=dark teal, FS=slate, Summary=navy
- **Number formats owned by formatter**: DKKk=`#,##0`, PCT=`0.0%`, RATIO=`0.00x`

### Cover sheet (cover_writer.py)
- View selector `F5`: 1=Bank, 2=IC (default), 3=Audit
- KPI values use `IF($F$5=...)` formulas referencing Summary sheet
- FX rate input cell `J2` (default 7.46)
- Color coding key legend rows 30-34

### Key assembly files
| File | Role |
|---|---|
| `cell_mapper.py` | Column constants, ROW_MAP, MODULE_SHEET, FIELD_LABELS, SUBSECTION_LABELS |
| `excel_writer.py` | Creates 7 sheets, writes data, calls formatter + cover writer |
| `excel_formatter.py` | All visual formatting (colors, borders, grouping, heights, print, tabs) |
| `cover_writer.py` | Cover sheet with perspective KPI block |

## Module pattern

Every module: single `.py` file with docstring header, `Inputs(BaseModel)`, `Outputs(BaseModel)`, `calculate()`, `get_excel_formulas()`.

```python
"""
MODULE_ID:    XXX_001
VERSION:      1.0
TIER:         detailed | both
MARKETS:      ["DK", "DE", ...]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      YYYY-MM-DD
MODIFIED:     YYYY-MM-DD
"""
class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    @model_validator(mode="after")
    def _validate(self): ...  # array length checks, return self

class Outputs(BaseModel): ...

def calculate(inputs: Inputs) -> Outputs: ...
def get_excel_formulas(refs: dict) -> dict: ...
```

## Common patterns

**Inflation:** `factor = start_factor * (1 + rate) ** max(0, year - start_year)`

**Year groups:** `_year_groups(periods, start_year, start_month) -> OrderedDict`

**Error handling:** Reject financially nonsensical inputs in `@model_validator` with clear `ValueError`. Add `warnings: list[str]` to Outputs for edge cases that aren't errors.

## How to add a new module

1. Create `modules/<category>/XXX_NNN.py` (follow existing module in same category). Set `CREATED:` and `MODIFIED:` to today's date.
2. Create `tests/test_<category>/test_XXX_NNN.py`
3. Add registry entry to `registry/module_registry.json` with `"created"` and `"modified"` set to today's date.
4. Add row mappings to `assembly/cell_mapper.py` (ROW_MAP, FIELD_LABELS, MODULE_SHEET)
5. Wire into `assembly/engine.py` (import, add to ProjectConfig, add step in `run()`)
6. `python -m pytest tests/ -q` — all tests must pass

## How to extend an existing module

1. Add new Inputs fields with defaults (existing configs don't break)
2. Add new Outputs fields (append only)
3. Update `calculate()` and `get_excel_formulas()`
4. Add tests, update `cell_mapper.py`, run all tests

## Common file references

| Task | File |
|---|---|
| Module logic | `modules/<category>/XXX_NNN.py` |
| Module wiring | `assembly/engine.py` — `run()` |
| Excel row/col layout | `assembly/cell_mapper.py` |
| Excel data writing | `assembly/excel_writer.py` |
| Excel visual formatting | `assembly/excel_formatter.py` |
| Cover sheet | `assembly/cover_writer.py` |
| Market assumptions | `registry/assumption_db/XX.json` |
| Module registry | `registry/module_registry.json` |
| Scenario analysis | `assembly/scenario_runner.py`, `assembly/scenario_engine.py` |

## Progressive disclosure — read when needed

- `cat docs/CLAUDE_MODULES.md` — module inventory, engine execution order, wiring mechanism, auto-wiring, debt ordering
- `cat docs/CLAUDE_SCHEMAS.md` — ProjectConfig fields, v2.0 target schemas, Excel formatting spec, cell mapper, assumption DB templates

## Style

- No comments on obvious code. Comments for non-obvious business logic only.
- Domain names: `n` for periods, `yg` for year_groups, `ir` for inflation_rate.
- `_helper()` with underscore for internal helpers. Type hints on signatures, not on locals.
- Test names: `test_<section>_<what>`.

## Git

- `main` branch, Martin reviews directly.
- Commit: `module_id: description` e.g. `DEBT_001: add margin schedule`.
- Never force-push. Full tests before every commit.
- When modifying a module: update `MODIFIED:` in the docstring and `"modified"` in module_registry.json to today's date.
- When bumping VERSION: update MODIFIED date in both places.

## When compacting

Always preserve: list of modified files, test commands, current module being worked on, and the v1.x/v2.0 transition state.
