# EE Model Library

Python module library for renewable energy project finance — European Energy A/S.

**Version:** v0.019 | **Last pushed:** 2026-03-31 | **Tests:** 1254 passed | **Modules:** 40 | **Branch:** `main`

---

## Architecture

Three-layer system:

```
FRONTEND  (Claude Project — EE Model Builder)
    |  JSON / .xlsx
INTELLIGENCE  (Claude + system prompt)
    |  HTTP POST /run
BACKEND  (this repo — ee-model-library)
    +-- assembly/engine.py -> excel_writer.py -> excel_formatter.py -> .xlsx
```

The assembly engine runs all enabled modules in dependency order and produces a bank-grade `.xlsx` workbook with 7 sheets, EE/F1F9 formatting standard, and perspective-aware Cover sheet.

---

## Repository Structure

```
ee-model-library/
  api/                        FastAPI app (POST /run, GET /modules, GET /health)
  assembly/
    engine.py                 Orchestration — runs 39 modules in dependency order
    cell_mapper.py            Row/column layout, column constants, SUBSECTION_LABELS
    excel_writer.py           openpyxl writer — 7-sheet .xlsx with formula layer
    excel_formatter.py        EE/F1F9 format standard (colors, borders, grouping, print)
    cover_writer.py           Cover sheet — Bank/IC/Audit perspective KPI blocks
    parser.py                 .xlsx assumption reader (named ranges + vendor models)
    scenario_runner.py        Multi-scenario runner + portfolio aggregation
    scenario_engine.py        Sensitivity engine + tornado charts
  modules/
    core/                     WACC_001
    market/                   PRICE_CURVES_001
    revenue/                  REV_001 (PV), REV_002 (BESS), REV_003 (WIND), PPA_CFD_001
    costs/                    OPEX_001-003, DECOM_PROVISION_001, IMBALANCE_FEE_001
    capex/                    CAPEX_001, BESS_REPOW_001
    debt/                     DEBT_001, DEBT_SCULPT_001, DEBT_LINEAR_001, DEBT_REFI_001,
                              CONSTR_FINANCE_001, SHL_001, VAT_FACILITY_001, DSRA_001,
                              REPOW_DEBT_001, CASH_SWEEP_001, BRIDGE_FACILITY_001, MRA_001
    tax/                      TAX_001 (DK), TAX_DE_001 (DE), TAX_LT_001 (LT), TAX_AU_001 (AU)
    statements/               PL_001, CF_001, BS_001, IRR_001, WORKING_CAPITAL_001,
                              SOURCES_USES_001, VALUATION_001, BREAKEVEN_001
    checks/                   MODEL_CHECKS_001
    reporting/                DASHBOARD_001
  registry/
    module_registry.json      Module index (40 modules, with created/modified dates)
    assumption_db/            Market assumptions (DK.json, DE.json, AU.json)
  calibration/
    deal_db.py                Deal database utility — auto-extracts KPIs + assumptions
    deal_db.json              6 calibrated deals
    run_01_holmen_ii.py       Holmen II DK Hybrid (PV+BESS) calibration
    run_02_master_model.py    Master Model DK Hybrid (sculpted debt) calibration
    run_03_upper_calliope.py  Upper Calliope AU PV+BESS (engine-generated)
    run_03_skuodas.py         Skuodas LT Hybrid (Wind+Solar) calibration
    run_04_viuf.py            Viuf DK PV+BESS (EY-reviewed) calibration
    run_05_mulwala.py         Mulwala AU BESS (Lancaster Risk) calibration
  tests/                      1254 automated tests
  docs/
    CLAUDE_MODULES.md         Full module inventory + engine wiring
    CLAUDE_SCHEMAS.md         ProjectConfig, Excel spec, assumption DB schemas
  contributions/
    TEMPLATE.md               Staging template for new module proposals
  requirements.txt
```

---

## Excel Output (7-Sheet Workbook)

| Sheet | Contents |
|-------|----------|
| Cover | Project banner, view selector (Bank/IC/Audit), KPI block, color key |
| Revenue | REV_001, REV_002, REV_003, PPA_CFD_001, PRICE_CURVES_001 |
| Costs | OPEX_001-003, CAPEX_001, BESS_REPOW_001, DECOM_PROVISION_001, IMBALANCE_FEE_001 |
| Debt | All DEBT_*, SHL_001, VAT_FACILITY_001, DSRA_001, MRA_001, TAX_*, WACC_001 |
| FS_Monthly | PL_001, CF_001, BS_001, IRR_001 + working capital, sources/uses, valuation |
| FS_Annual | Annual aggregation (flows=SUM, stocks=closing balance) |
| Summary | Scalar KPIs (IRR, NPV, DSCR, WACC) |

**Column layout (EE Standard):** A-D spacers, E label, F constant, G unit, H-I hidden notes/source, J total, K spacer, L+ monthly time series.

**Format standard:** Dual-mode F1F9 (default) or EE Legacy. F1F9: Arial 10pt, grey #808080 borders, dd mmm yy dates, #DDDDDD section fills, zoom 80%, gridlines off, A4 55% scale, conditional formatting. Style Guide sheet auto-generated.

---

## Module Registry

| Category | Modules | Count |
|----------|---------|-------|
| Core | WACC_001 | 1 |
| Market | PRICE_CURVES_001 | 1 |
| Revenue | REV_001, REV_002, REV_003, PPA_CFD_001 | 4 |
| Costs | OPEX_001-003, DECOM_PROVISION_001, IMBALANCE_FEE_001 | 5 |
| CAPEX | CAPEX_001, BESS_REPOW_001 | 2 |
| Debt | DEBT_001, DEBT_SCULPT_001, DEBT_LINEAR_001, DEBT_REFI_001, CONSTR_FINANCE_001, SHL_001, VAT_FACILITY_001, DSRA_001, REPOW_DEBT_001, CASH_SWEEP_001, BRIDGE_FACILITY_001, MRA_001 | 12 |
| Tax | TAX_001 (DK), TAX_DE_001 (DE), TAX_LT_001 (LT), TAX_AU_001 (AU) | 4 |
| Statements | PL_001, CF_001, BS_001, IRR_001, WORKING_CAPITAL_001, SOURCES_USES_001, VALUATION_001, BREAKEVEN_001, DIV_001 | 9 |
| Checks | MODEL_CHECKS_001 | 1 |
| Reporting | DASHBOARD_001 | 1 |
| **Total** | | **40** |

---

## Quick Start

### Run the API

```bash
pip install -r requirements.txt uvicorn
uvicorn api.main:app --reload
```

Interactive docs at `http://localhost:8000/docs`.

### Run all tests

```bash
python -m pytest tests/ -q
```

### Assemble a model programmatically

```python
from assembly.engine import ProjectConfig, TimelineConfig, TaxConfig, StatementConfig, run
from assembly.excel_writer import write_workbook
import modules.revenue.REV_001 as REV_001

config = ProjectConfig(
    project_name="My PV Project",
    timeline=TimelineConfig(periods=300, start_year=2026, start_month=1),
    rev_pv=REV_001.Inputs(
        periods=300, start_year=2026, start_month=1,
        net_production_MWh=[...],
        wholesale_price_DKK_per_MWh=[...],
        capture_price_DKK_per_MWh=[...],
        goo_price_DKK_per_MWh=[...],
    ),
    tax=TaxConfig(country="DK"),
)

result = run(config)
write_workbook(result, config, "output/My_PV_Project.xlsx")
```

---

## Calibration

6 calibrated deals across 3 markets, stored in `calibration/deal_db.json`:

| Run | Deal | Market | Technology | Key KPIs |
|-----|------|--------|------------|----------|
| 01 | Holmen II | DK | PV+BESS | Project IRR 3.92%, 3 linear debt facilities |
| 02 | Master Model | DK | Hybrid | Sculpted debt, iterative sizing, 15 gaps closed |
| 03 | Upper Calliope | AU | PV+BESS | Engine-generated, EBITDA 115k, 5/5 KPIs pass |
| 03 | Skuodas | LT | Wind+Solar | IRR 8.85%, DSCR 1.45x, EBITDA cap, 7/7 KPIs pass |
| 04 | Viuf | DK | PV+BESS | EBITDA 5,908k, revenue 7,155k, EY-reviewed |
| 05 | Mulwala | AU | BESS | Project IRR 9.97%, VTA compensation, FCAS |

## Recent Changes (v0.019 — Sessions 12-16)

| Change | Detail |
|--------|--------|
| **TAX_AU_001** (NEW) | 30% AU corporate tax, straight-line depreciation, full engine wiring |
| **REV_002 v1.1** | VTA compensation (4 layers) + FCAS regulation/contingency revenue |
| **SHL_001 v1.1** | Configurable PIK compounding frequency (1=monthly, 6=semi-annual) |
| **TAX_001 v1.2** | Qualifying assets interest deduction rule |
| **TAX_LT_001 v1.1** | EBITDA cap (30%), SHL interest limit, unlevered tax output |
| **F1F9 formatting** | Full F1F9 layout standard: Arial, grey borders, dd mmm yy dates, zoom 80%, CF rules |
| **Engine FCFE** | Equity IRR uses correct levered FCFE (fixes CF interest double-count) |
| **Deal DB** | `save_to_deal_db()` — 6 deals across DK/LT/AU |
| **UC Master Model** | Engine-generated v2 with F1F9 formatting |

---

## Contribution Flow

1. New logic detected in an operator-uploaded model
2. Claude proposes a module ID and spec in `contributions/staged/`
3. Martin reviews and approves
4. Module committed to `modules/` and registered in `module_registry.json`

Every module must have: Pydantic Inputs/Outputs, pure-Python `calculate()`, `get_excel_formulas()`, and a passing test suite.

---

*Maintained by Martin Graa Jennum, European Energy A/S*
