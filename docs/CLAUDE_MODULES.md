# Module Inventory & Engine Wiring

Read this file when working on module wiring, engine.py, or adding/modifying modules.

## Current module inventory (v1.x)

### Revenue

| Module ID | File | Purpose |
|---|---|---|
| `REV_001` | `modules/revenue/REV_001.py` | PV — 8-section: production, price indexation, spot, 5 PPAs, GoO, tariffs, balancing, summary |
| `REV_002` | `modules/revenue/REV_002.py` | BESS — discharge × spread, charging, multi-market, tolling |
| `REV_003` | `modules/revenue/REV_003.py` | Wind — same structure as REV_001, different capture dynamics |
| `PPA_CFD_001` | `modules/revenue/PPA_CFD_001.py` | CfD overlay on merchant revenue |
| `PRICE_CURVES_001` | `modules/market/PRICE_CURVES_001.py` | External price curve pass-through |

### Costs

| Module ID | File | Purpose |
|---|---|---|
| `OPEX_001` | `modules/costs/OPEX_001.py` | PV OPEX — 11 components |
| `OPEX_002` | `modules/costs/OPEX_002.py` | BESS OPEX — 4 components |
| `OPEX_003` | `modules/costs/OPEX_003.py` | Wind OPEX — 5 components |
| `CAPEX_001` | `modules/capex/CAPEX_001.py` | Capital expenditure — 6 buckets, S-curve, repowering |
| `BESS_REPOW_001` | `modules/capex/BESS_REPOW_001.py` | BESS battery repowering — cost spike, depreciation |

### Debt & financing

| Module ID | File | Purpose |
|---|---|---|
| `DEBT_001` | `modules/debt/DEBT_001.py` | Senior — annuity/straight-line/bullet, DSCR, margin schedule |
| `DEBT_SCULPT_001` | `modules/debt/DEBT_SCULPT_001.py` | Sculpted — DSCR-based sizing, per-stream blending, auto-sizing |
| `DEBT_LINEAR_001` | `modules/debt/DEBT_LINEAR_001.py` | Multi-facility linear |
| `CONSTR_FINANCE_001` | `modules/debt/CONSTR_FINANCE_001.py` | Construction finance — equity-first, IDC, bullet at COD |
| `SHL_001` | `modules/debt/SHL_001.py` | Shareholder loan — PIK interest, subordinated |
| `VAT_FACILITY_001` | `modules/debt/VAT_FACILITY_001.py` | Construction VAT revolving |
| `DEBT_REFI_001` | `modules/debt/DEBT_REFI_001.py` | Sculpted refinancing — wired from DEBT_SCULPT_001 |
| `REPOW_DEBT_001` | `modules/debt/REPOW_DEBT_001.py` | BESS repowering debt |
| `DSRA_001` | `modules/debt/DSRA_001.py` | Debt service reserve account (cash or facility mode) |

### Tax

| Module ID | File | Purpose |
|---|---|---|
| `TAX_001` | `modules/tax/TAX_001.py` | Denmark — declining balance, EBITDA cap, loss c/f, 7 buckets |
| `TAX_DE_001` | `modules/tax/TAX_DE_001.py` | Germany — KStG + solidarity + trade tax |

### Financial statements & core

| Module ID | File | Purpose |
|---|---|---|
| `PL_001` | `modules/statements/PL_001.py` | Profit & Loss |
| `CF_001` | `modules/statements/CF_001.py` | Cash Flow (indirect method) |
| `BS_001` | `modules/statements/BS_001.py` | Balance Sheet |
| `IRR_001` | `modules/statements/IRR_001.py` | DCF valuation — bisection solver |
| `WACC_001` | `modules/core/WACC_001.py` | Two-layer WACC (PPA/merchant, Hamada) |
| `WORKING_CAPITAL_001` | `modules/statements/WORKING_CAPITAL_001.py` | Working capital movement |
| `SOURCES_USES_001` | `modules/statements/SOURCES_USES_001.py` | Sources & uses during construction |
| `VALUATION_001` | `modules/statements/VALUATION_001.py` | Enterprise valuation (DCF) |
| `BREAKEVEN_001` | `modules/statements/BREAKEVEN_001.py` | Breakeven / sensitivity |
| `DIV_001` | `modules/statements/DIV_001.py` | Dividend distribution + capital reduction (3-gate waterfall) |
| `MODEL_CHECKS_001` | `modules/checks/MODEL_CHECKS_001.py` | BS balance, DSCR covenant, IS consistency |
| `DASHBOARD_001` | `modules/reporting/DASHBOARD_001.py` | KPI dashboard |

### Assembly layer

| File | Purpose |
|---|---|
| `assembly/engine.py` | Orchestrates all modules |
| `assembly/cell_mapper.py` | Module/field → Excel row/col, column constants, SUBSECTION_LABELS |
| `assembly/excel_writer.py` | openpyxl → 7-sheet .xlsx workbook |
| `assembly/excel_formatter.py` | EE/F1F9 format standard (colors, borders, grouping, heights, print, tabs) |
| `assembly/cover_writer.py` | Cover sheet with perspective-aware KPI blocks (Bank/IC/Audit) |
| `assembly/parser.py` | Reads operator .xlsx |
| `assembly/perspectives.py` | Multi-perspective output (stubbed — not yet implemented) |
| `assembly/scenario_runner.py` | Multi-scenario runner + portfolio aggregation + 2D sensitivity |
| `assembly/scenario_engine.py` | Sensitivity engine + tornado charts + debt-resize mode |
| `assembly/debt_solver.py` | Iterative sculpted debt sizing with convergence loop |

---

## Engine wiring mechanism

`engine.run()` maintains `out: dict[str, Any]` keyed by module ID. Each module receives only its own Inputs. The engine extracts upstream outputs and injects into downstream Inputs.

```python
def run(config: ProjectConfig) -> AssemblyResult:
    out = result.outputs  # dict keyed by module ID

    # User-supplied modules: engine passes config directly
    if config.rev_pv is not None:
        out["REV_001"] = REV_001.calculate(_inject_timeline(config.rev_pv, tl))

    # Auto-wired modules: engine builds inputs from upstream outputs
    if config.constr_finance is not None:
        cf_inp = config.constr_finance
        if "CAPEX_001" in out:
            cf_inp = cf_inp.model_copy(update={
                "capex_monthly": out["CAPEX_001"].total_capex_monthly,
            })
        out["CONSTR_FINANCE_001"] = CONSTR_FINANCE_001.calculate(...)

    # Fully wired modules: engine constructs entire Inputs
    # TAX_001, PL_001, CF_001, BS_001, IRR_001
```

**Key rules:**
- Modules never import other modules
- `_inject_timeline(inp, tl)` overrides periods/start_year/start_month before every `calculate()`
- If upstream is disabled (None), engine substitutes zero-arrays
- Steps 1–9: user-supplied. Steps 10+: engine-wired.

---

## Engine execution order (matches engine.py)

```
Step 0:   PRICE_CURVES_001     market price curves
Step 1:   WACC_001             scalar outputs
Step 2:   REV_001              PV revenue
Step 3:   REV_002              BESS revenue
Step 4:   REV_003              Wind revenue
Step 4.5: PPA_CFD_001          CfD overlay
Step 5:   OPEX_001             PV OPEX
Step 6:   OPEX_002             BESS OPEX
Step 7:   OPEX_003             Wind OPEX
Step 8:   CAPEX_001            capital expenditure
Step 8b:  BESS_REPOW_001       battery repowering
Step 9a:  CONSTR_FINANCE_001   auto-wires capex from CAPEX_001
Step 9b:  DEBT_001             senior debt
Step 9c:  DEBT_SCULPT_001      sculpted (needs CFADS from rev)
Step 9d:  DEBT_REFI_001        auto-wires balance from DEBT_SCULPT_001
Step 9e:  DEBT_LINEAR_001      multi-facility linear
Step 9.5: SHL_001              shareholder loan
Step 9.6: VAT_FACILITY_001     auto-wires capex from CAPEX_001
Step 9.7: DSRA_001             auto-wires debt_service from DEBT_001
Step 9e:  REPOW_DEBT_001       repowering debt
Step 10:  TAX_001              WIRED: ebitda, interest, capex_by_bucket
Step 10b: TAX_DE_001           German tax (alternative)
Step 11:  PL_001               WIRED: rev, opex, tax, debt (sums interest from DEBT_001 + REFI + LINEAR + CONSTR)
Step 12:  CF_001               WIRED: PL, capex, debt
Step 13:  BS_001               WIRED: capex, CF, debt (sums closing_balance from DEBT_001 + REFI + LINEAR + CONSTR)
Step 14:  IRR_001              WIRED: CF, WACC
Step 14.5: WORKING_CAPITAL_001
Step 14.6: SOURCES_USES_001
Step 14.7: VALUATION_001
Step 14.8: BREAKEVEN_001
Step 14.9: DASHBOARD_001
Step 15:  MODEL_CHECKS_001     WIRED: BS imbalance, DSCR, IS consistency
```

### Auto-wiring details

| Module | Wired from | What's injected |
|---|---|---|
| `CONSTR_FINANCE_001` | `CAPEX_001` | `capex_monthly` |
| `DEBT_REFI_001` | `DEBT_SCULPT_001` | `original_balance_at_refi_DKKk` (closing balance at refi period) |
| `VAT_FACILITY_001` | `CAPEX_001` | `capex_monthly` |
| `DSRA_001` | `DEBT_001` | `debt_service` |
| `TAX_001` | `REV_*`, `OPEX_*`, `DEBT_*`, `CAPEX_001` | ebitda, total_interest, capex_by_bucket |
| `PL_001` | `REV_*`, `OPEX_*`, `TAX_*`, `DEBT_*`, `BESS_REPOW_001` | gross_rev, total_opex, dep, interest, tax |
| `CF_001` | `PL_001`, `CAPEX_001`, `DEBT_*`, `BESS_REPOW_001` | net_income, dep, capex, drawdowns, principal, interest |
| `BS_001` | `CAPEX_001`, `CF_001`, `DEBT_*`, `PL_001`, `BESS_REPOW_001` | capex, cash, debt balances, net_income |
| `IRR_001` | `CF_001`, `WACC_001` | pfcf (CFO+CFI), ecf, discount rates |

---

## Scenario engines

### scenario_runner.py
- `run_scenarios(base, scenarios)` — applies `ScenarioOverride` dot-path parameter changes
- `aggregate_portfolio(results, weights)` — capex-weighted aggregation
- `run_sensitivity_table(base, row_axis, col_axis, kpi)` — 2D sensitivity grid

### scenario_engine.py
- `run_scenarios(config)` — `SensiParameter` (pct_delta, absolute, switch)
- `resize_debt` mode: debt auto-sizes to new CFADS per scenario
- Outputs: `ScenarioKPIs` per scenario + tornado data
- KPIs: IRR, DSCR, LLCR, gearing, capex/MW, EV

Both extract KPIs using current module IDs: `out["REV_001"]`, `out["DEBT_SCULPT_001"]`, etc.

---

## Testing scale

| Module type | Minimum tests |
|---|---|
| Debt modules | 15+ each |
| Revenue modules | 15+ each |
| OPEX modules | 10+ each |
| Statement modules (PL, CF, BS) | 10+ |
| Tax modules | 10+ per jurisdiction |
| Pass-through (PRICE_CURVES, DASHBOARD) | 3–5 |
| Integration tests | 1 per calibration run |
