# Changelog

All notable changes to the EE Model Library.

---

## v1.0.0 — 2026-03

### Added

**14 calculation modules:**
- `WACC_001` — Two-layer PPA/merchant cost of capital
- `REV_001` — PV revenue (spot, PPA, GoO, production costs, balancing)
- `REV_002` — BESS revenue (discharge, charging, tariffs, multimarket)
- `REV_003` — Wind revenue (spot, PPA, GoO, production costs, balancing)
- `OPEX_001` — PV OPEX (11 components)
- `OPEX_002` — BESS OPEX (4 components)
- `OPEX_003` — Wind OPEX (5 components)
- `CAPEX_001` — Capital expenditure schedule (6 buckets, S-curve drawdown)
- `DEBT_001` — Senior debt schedule (annuity/straight-line/bullet, DSCR)
- `TAX_001` — Danish corporate tax (declining balance, EBITDA cap, loss c/f)
- `PL_001` — Profit & Loss statement
- `CF_001` — Cash Flow statement (indirect method)
- `BS_001` — Balance Sheet
- `IRR_001` — DCF valuation (IRR, NPV, payback — bisection solver, no external deps)

**Assembly layer:**
- `engine.py` — Orchestration engine with dependency wiring for all 14 modules
- `cell_mapper.py` — Row/column layout for the 5-sheet Excel workbook
- `excel_writer.py` — openpyxl workbook writer (Revenue, Costs, Debt, Statements, Summary)
- `parser.py` — Operator model reader (named ranges + Assumptions sheet strategies)

**API:**
- `api/main.py` — FastAPI app with `POST /run`, `GET /modules`, `GET /health`

**Market assumption databases:**
- `registry/assumption_db/DK.json` — Denmark
- `registry/assumption_db/DE.json` — Germany
- `registry/assumption_db/AU.json` — Australia

**Tests:**
- 500+ automated tests covering all modules, assembly layer, and integration

**Documentation:**
- `docs/MODULE_GUIDE.md` — Module design rules and patterns
- `docs/ASSEMBLY_GUIDE.md` — Assembly layer usage guide

### Architecture decisions

- Monthly time-series throughout (period 0 = first month of project)
- Pydantic v2 for all Inputs/Outputs with length validation
- Pure Python — no pandas, no numpy, no external solvers
- IRR solved by bisection on NPV = 0 (monthly rate range −50% to +100%)
- Sequential module numbering: different numbers = different technologies, not versions
- WACC uses two-layer model: PPA revenue has reduced ERP, merchant has full ERP
