# EE Model Library

Python module library for renewable energy project finance — European Energy A/S.

**Status:** v1.0 — 14 modules, 3 technologies (PV, BESS, WIND), 5 markets (DK, DE, AU, SE, PL)

---

## Architecture

Three-layer system:

```
FRONTEND  (Claude Project — EE Model Builder)
    ↕  JSON / .xlsx
INTELLIGENCE  (Claude + system prompt)
    ↕  HTTP POST /run
BACKEND  (this repo — ee-model-library)
    └── assembly/engine.py → excel_writer.py → .xlsx workbook
```

The Claude-based EE Model Builder sends a `ProjectConfig` JSON payload to the API, which runs all enabled modules in dependency order and returns a fully populated `.xlsx` workbook.

---

## Repository Structure

```
ee-model-library/
  api/                    FastAPI app (POST /run, GET /modules, GET /health)
  assembly/
    engine.py             Orchestration — runs modules in dependency order
    cell_mapper.py        Row/column layout for the 5-sheet workbook
    excel_writer.py       openpyxl writer — produces the .xlsx output
    parser.py             .xlsx assumption reader (named ranges + Assumptions sheet)
  modules/
    core/                 WACC_001 — cost of capital
    revenue/              REV_001 (PV), REV_002 (BESS), REV_003 (WIND)
    costs/                OPEX_001 (PV), OPEX_002 (BESS), OPEX_003 (WIND)
    capex/                CAPEX_001 — capital expenditure schedule
    debt/                 DEBT_001 — senior debt schedule
    tax/                  TAX_001 — Danish corporate tax
    statements/           PL_001, CF_001, BS_001, IRR_001
  registry/
    module_registry.json  Module index (id, version, markets, technologies, path)
    assumption_db/        Market assumption databases (DK.json, DE.json, AU.json)
  tests/                  500+ automated tests
  docs/                   Module guide, assembly guide, changelog
  requirements.txt
```

---

## Module Registry

| ID | Description | Technology | Markets |
|----|-------------|------------|---------|
| WACC_001 | Weighted Average Cost of Capital (two-layer PPA/merchant) | * | DK, DE, * |
| REV_001 | PV Revenue (spot, PPA slots, GoO, tariffs, balancing) | PV | DK, DE, AU, SE, * |
| REV_002 | BESS Revenue (discharge, charging, import/export tariffs) | BESS | DK, DE, * |
| REV_003 | Wind Revenue (spot, PPA slots, GoO, tariffs, balancing) | WIND | DK, DE, SE, PL, * |
| OPEX_001 | PV OPEX (11 components: O&M, land, insurance, VE-bonus…) | PV | DK, DE, AU, SE, * |
| OPEX_002 | BESS OPEX (O&M, insurance, trading costs, other) | BESS | DK, DE, * |
| OPEX_003 | Wind OPEX (O&M, land lease, insurance, grid costs, other) | WIND | DK, DE, SE, PL, * |
| CAPEX_001 | Capital Expenditure (EPC, grid, development, contingency) | * | DK, DE, AU, SE, PL, * |
| DEBT_001 | Senior Debt Schedule (annuity/straight-line/bullet, DSCR) | * | DK, DE, AU, SE, PL, * |
| TAX_001 | Danish Corporate Tax (declining balance dep., EBITDA cap) | * | DK |
| PL_001 | Profit & Loss Statement | * | DK, DE, AU, SE, PL, * |
| CF_001 | Cash Flow Statement (indirect method) | * | DK, DE, AU, SE, PL, * |
| BS_001 | Balance Sheet | * | DK, DE, AU, SE, PL, * |
| IRR_001 | DCF Valuation (project IRR, equity IRR, NPV, payback) | * | DK, DE, AU, SE, PL, * |

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

## Contribution Flow

1. New logic detected in an operator-uploaded model
2. Claude proposes a module ID and spec in `contributions/staged/`
3. Martin reviews and approves
4. Module committed to `modules/` and registered in `module_registry.json`

Every module must have: Pydantic Inputs/Outputs, pure-Python `calculate()`, `get_excel_formulas()`, and a passing test suite.

---

*Maintained by Martin Graa Jennum, European Energy A/S*
