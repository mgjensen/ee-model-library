# CLAUDE.md — Instructions for Claude Code

This file tells Claude Code how to work with the `ee-model-library` repository.
Place it in the repo root. Claude Code reads it automatically.

---

## What this repo is

A Python library that assembles production-ready Excel financial models for renewable energy projects (solar PV, BESS, wind). It has 14 calculation modules, an assembly engine that wires them together, and an Excel writer that produces `.xlsx` output.

**Owner:** Martin Graa Jennum, European Energy A/S
**Language:** Python 3.11+, pure Python (no numpy/pandas in modules)
**Key dependency:** Pydantic v2 for all Inputs/Outputs

---

## Project structure

```
ee-model-library/
  api/main.py                    FastAPI app (POST /run, GET /modules, GET /health)
  assembly/
    engine.py                    Orchestrates 14 modules in dependency order
    cell_mapper.py               (module, field) → Excel row/col position
    excel_writer.py              openpyxl → 5-sheet .xlsx workbook
    parser.py                    Reads operator .xlsx files into assumption dict
  modules/
    core/WACC_001.py             Cost of capital (two-layer PPA/merchant)
    revenue/REV_001.py           PV revenue (spot, 5 PPAs, GoO, tariffs, balancing)
    revenue/REV_002.py           BESS revenue (discharge, charging, tariffs)
    revenue/REV_003.py           Wind revenue (same structure as REV_001)
    costs/OPEX_001.py            PV OPEX (11 components)
    costs/OPEX_002.py            BESS OPEX (4 components)
    costs/OPEX_003.py            Wind OPEX (5 components)
    capex/CAPEX_001.py           6 cost buckets, S-curve drawdown
    debt/DEBT_001.py             Senior debt (annuity/straight-line/bullet, DSCR)
    tax/TAX_001.py               Danish corporate tax (declining balance, EBITDA cap)
    statements/PL_001.py         Profit & Loss
    statements/CF_001.py         Cash Flow (indirect method)
    statements/BS_001.py         Balance Sheet
    statements/IRR_001.py        DCF valuation (bisection IRR solver)
  registry/
    module_registry.json         Master index of all modules
    assumption_db/DK.json        Denmark market assumptions
    assumption_db/DE.json        Germany
    assumption_db/AU.json        Australia
  tests/                         516 pytest tests
  contributions/staged/          Draft modules awaiting Martin's review
  docs/MODULE_GUIDE.md           Module design patterns
  docs/ASSEMBLY_GUIDE.md         Assembly layer usage
  docs/CHANGELOG.md
  requirements.txt               openpyxl, pydantic, pandas, pytest, fastapi
```

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests (always do this before committing)
python -m pytest tests/ -q

# Run specific module tests
python -m pytest tests/test_revenue/test_REV_001.py -v
python -m pytest tests/test_debt/test_DEBT_001.py -v

# Run integration tests
python -m pytest tests/test_integration/ -v

# Start API locally
pip install uvicorn
uvicorn api.main:app --reload
# Then open http://localhost:8000/docs
```

---

## Module pattern — FOLLOW THIS EXACTLY

Every module is a single `.py` file. Every module MUST have these four things:

### 1. Docstring header

```python
"""
MODULE_ID:    XXX_001
VERSION:      1.0
TIER:         detailed | both
MARKETS:      ["DK", "DE", ...]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

One-line description.
"""
```

### 2. Pydantic Inputs class

```python
class Inputs(BaseModel):
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)
    # ... module-specific fields ...

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        # Validate all time-series arrays have length == periods
        for name, arr in [("field_name", self.field_name)]:
            if len(arr) != n:
                raise ValueError(f"{name} length {len(arr)} != periods {n}")
        return self
```

### 3. Pydantic Outputs class

```python
class Outputs(BaseModel):
    # Time-series: list[float] with length == periods
    # Scalars: float
    # Annual aggregation: list[float] with length == number of calendar years
```

### 4. Functions

```python
def calculate(inputs: Inputs) -> Outputs:
    """Core computation — pure Python, no side effects, no external deps."""
    ...

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for key output rows."""
    ...
```

---

## STRICT RULES — do not violate these

1. **Pure Python only** in modules. No numpy, no pandas, no scipy, no external solvers. Use `math` from stdlib if needed.
2. **All time-series outputs must have length == `inputs.periods`**. Period 0 is the first month of the project.
3. **Pydantic v2 conventions**: `Field(...)` for required, `Field(default, description="...")` for optional. Use `@model_validator(mode="after")` for array length checks. Always `return self` at end of validator.
4. **Inflation/indexation pattern** (used everywhere):
   ```python
   factor(year) = start_factor * (1 + inflation_rate) ** max(0, year - inflation_start_year)
   ```
5. **Year grouping pattern** (used everywhere):
   ```python
   def _year_groups(periods, start_year, start_month) -> OrderedDict:
       groups = OrderedDict()
       for p in range(periods):
           offset = start_month - 1 + p
           year = start_year + offset // 12
           groups.setdefault(year, []).append(p)
       return groups
   ```
6. **Sequential module numbering**: REV_004 = new PV revenue module. NOT REV_001 v2. Different numbers = different technologies or approaches.
7. **Currency unit**: DKKk (thousands of DKK) throughout, except where noted (DKK/MWh for prices).
8. **Never break existing tests.** Run `python -m pytest tests/ -q` before any commit. All 516 tests must pass.
9. **Never modify existing module Outputs in a breaking way.** Add new fields, don't rename or remove existing ones. Other modules and the assembly engine depend on them.
10. **engine.py execution order matters.** Steps 1–9 are user-supplied inputs. Steps 10–14 are auto-wired. If you add a new module, decide where it fits in the chain.

---

## Engine execution order (dependency chain)

```
Step 1:  WACC_001    no dependencies (scalar outputs)
Step 2:  REV_001     PV revenue
Step 3:  REV_002     BESS revenue
Step 4:  REV_003     Wind revenue
Step 5:  OPEX_001    PV OPEX
Step 6:  OPEX_002    BESS OPEX
Step 7:  OPEX_003    Wind OPEX
Step 8:  CAPEX_001   capital expenditure
Step 9:  DEBT_001    debt schedule
Step 10: TAX_001     WIRED from: rev.net_revenue, opex.total_opex, debt.interest, capex
Step 11: PL_001      WIRED from: rev, opex, tax, debt
Step 12: CF_001      WIRED from: PL, capex, debt
Step 13: BS_001      WIRED from: capex, CF, debt, PL
Step 14: IRR_001     WIRED from: CF (pfcf = CFO+CFI, ecf = net_cash_flow), WACC rates
```

---

## How to add a new module (checklist)

When I ask you to create a new module, follow these steps in order:

1. **Create the module file** at `modules/<category>/XXX_NNN.py`
   - Follow the exact pattern from an existing module in the same category
   - Include docstring header, Inputs, Outputs, calculate(), get_excel_formulas()
   - Copy the `_year_groups()` and `_indexation_factor()` helpers if needed

2. **Create the test file** at `tests/test_<category>/test_XXX_NNN.py`
   - Test validation (wrong array lengths, invalid values)
   - Test output structure (returns Outputs, correct array lengths)
   - Test core calculations (known-value checks)
   - Test edge cases (zeros, large values, boundary conditions)
   - Minimum 15 tests per module

3. **Add registry entry** to `registry/module_registry.json`:
   ```json
   "XXX_NNN": {
     "module_id": "XXX_NNN",
     "version": "1.0",
     "tier": "detailed",
     "markets": ["DK", ...],
     "technologies": ["PV", ...],
     "path": "modules/<category>/XXX_NNN.py"
   }
   ```

4. **Add row mappings** to `assembly/cell_mapper.py`:
   - Add entries to `ROW_MAP` for each time-series output
   - Add entries to `FIELD_LABELS` for display names
   - Add entries to `FIELD_UNITS` if non-default units
   - Add to `MODULE_SHEET` to assign a worksheet

5. **Wire into engine.py**:
   - Import the module at top of file
   - Add `Optional[XXX_NNN.Inputs]` field to `ProjectConfig`
   - Add execution step in `run()` at the correct position in the dependency chain
   - If downstream modules need its outputs, wire them (e.g. tax needs debt.interest)

6. **Run all tests**: `python -m pytest tests/ -q` — every single test must pass

---

## How to extend an existing module

When I ask you to extend a module (add fields, add logic):

1. **Add new fields to Inputs** — always with defaults so existing configs don't break
2. **Add new fields to Outputs** — append only, never rename/remove
3. **Update calculate()** — add the new logic
4. **Update get_excel_formulas()** — add formula strings for new rows
5. **Add new tests** for the new functionality
6. **Update cell_mapper.py** — add ROW_MAP entries for new output fields
7. **Run all tests** — existing tests must still pass, new tests must pass too

---

## Common file references

| I want to... | Edit... |
|---|---|
| Add/change a module's logic | `modules/<category>/XXX_NNN.py` |
| Change how modules are wired | `assembly/engine.py` — the `run()` function |
| Change Excel row layout | `assembly/cell_mapper.py` — `ROW_MAP`, `FIELD_LABELS` |
| Add a market assumption DB | `registry/assumption_db/XX.json` |
| Add a module to the registry | `registry/module_registry.json` |
| Add/change API endpoints | `api/main.py` |

---

## Development backlog (prioritised)

These are known gaps from comparing the library against the EE vendor model (Holsted Hybrid, DK, PV+BESS). Work on these when I ask.

### High priority
- **DEBT_001 → add sculpted repayment**: Add `repayment_type="sculpted"` with per-revenue-stream DSCR weights (PV contracted 1.2×, PV merchant 1.5×, BESS contracted 1.2×, BESS merchant 1.8×). Also add debt auto-sizing (min of DSCR-sized and leverage-capped).
- **CAPEX_001 → add BESS repowering**: Mid-life battery stack replacement. New inputs: `repowering_cost_DKKk`, `repowering_period` (0-based), `repowering_drawdown_months`. Output: second capex event at the repowering period.

### Medium priority
- **New module: SHL_001** — Shareholder loan. Inputs: `shl_pct_of_equity`, `margin`, `accrued` (bool). Outputs: monthly opening/closing balance, interest (accrued or cash), repayment. Wire into CF_001 and BS_001.
- **New module: VAT_FACILITY_001** — Construction VAT financing. Inputs: VAT rate, reimbursement delay months, margin, commitment fee. Outputs: monthly VAT paid, VAT refund, facility balance, interest.
- **TAX_001 → expand to 7 buckets**: Currently 4 depreciation buckets. Extend to 7 (hard PV, hard BESS, grid, development, land, advisors, repowering) with per-bucket rates and lifetimes.
- **New module: REPOWERING_FACILITY_001** — Separate debt instrument for BESS repowering. Same structure as DEBT_001 but triggered at repowering date with 100% LTV of repowering cost.

### Low priority
- **REV_002 → add tolling agreement slot**: Contracted MW × price/MW/year, with inflation and tenor. Toggle on/off.
- **REV_002 → add external curve pass-through**: Accept pre-computed BESS revenue from technical model (Aurora/in-house) as direct input, bypassing internal calculation.
- **Assembly → multi-scenario engine**: Run ProjectConfig N times with parameter sweeps. Output comparison table.

---

## Debugging tips

After `result = run(config)`:

```python
# Inspect module output
rev = result.outputs["REV_001"]
print(rev.net_revenue[:12])        # first 12 months

# BS imbalance check (should be ~0)
bs = result.outputs["BS_001"]
max_imbalance = max(abs(v) for v in bs.imbalance)

# DSCR covenant check
debt = result.outputs["DEBT_001"]
print(f"Min DSCR: {debt.min_dscr:.2f}x, Breached: {debt.covenant_breached}")

# IRR
irr = result.outputs["IRR_001"]
print(f"Project IRR: {irr.project_irr:.2%}, Equity IRR: {irr.equity_irr:.2%}")
```

Common errors:
- `ValueError: drawdowns sum != facility` — drawdown schedule doesn't match facility amount
- `ValueError: xxx length N != periods M` — time-series array length mismatch
- `ppa_slots must have exactly 5 entries` — REV_001/REV_003 always expect 5 PPA slots

---

## Style conventions

- No comments on obvious code. Comments only for non-obvious business logic.
- Concise variable names matching the domain: `n` for periods, `yg` for year_groups, `ir` for inflation_rate.
- `_helper_functions()` with leading underscore for module-internal helpers.
- Type hints on function signatures. Not needed on local variables.
- Module docstrings reference the EE_MODEL_BUILD_SPEC section number.
- Test function names: `test_<section>_<what_it_checks>` e.g. `test_e_goo_volume_no_ppa`.

---

## Git workflow

- Work on `main` branch (Martin reviews directly)
- Commit messages: `module_id: short description` e.g. `DEBT_001: add sculpted repayment type`
- Never force-push
- Run full test suite before every commit
