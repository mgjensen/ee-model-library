# Module Guide

Design rules and patterns for all calculation modules in the EE Model Library.

---

## Module Anatomy

Every module is a single `.py` file with this structure:

```python
"""
MODULE_ID:    XXX_001
VERSION:      1.0
TIER:         detailed | both
MARKETS:      ["DK", "DE", ...]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      YYYY-MM-DD
MODIFIED:     YYYY-MM-DD

Short description.
"""

from pydantic import BaseModel, Field, model_validator

class Inputs(BaseModel):
    periods: int
    start_year: int
    start_month: int
    # ... module-specific fields

class Outputs(BaseModel):
    # ... all output fields as list[float] (time-series) or float (scalar)

def calculate(inputs: Inputs) -> Outputs:
    """Core computation — pure Python, no side effects."""
    ...

def get_excel_formulas(refs: dict) -> dict:
    """Return Excel formula strings for each output row."""
    ...
```

---

## Naming Convention

Modules are numbered sequentially within each category. Different numbers = different technologies, not different versions.

| Category | Module | Technology |
|----------|--------|-----------|
| Revenue | REV_001 | PV |
| Revenue | REV_002 | BESS |
| Revenue | REV_003 | WIND |
| OPEX | OPEX_001 | PV |
| OPEX | OPEX_002 | BESS |
| OPEX | OPEX_003 | WIND |

A second version of PV revenue would be `REV_004`, not `REV_001 v2`.

---

## Module Tiers

- **both** — used in both quick-look and detailed models (e.g. WACC_001)
- **detailed** — detailed models only

---

## Pydantic Conventions

- Required fields: `Field(...)` — no default
- Optional fields: `Field(default_value, description="...")`
- Length validation of time-series arrays in `@model_validator(mode="after")`
- Never modify `self` in validators — return `self` at the end

---

## Time-Series Convention

All time-series outputs have length == `inputs.periods`. Period 0 is the first month of the project (often the start of construction).

Annual aggregations (e.g. `annual_opex`) group periods by calendar year using `_year_groups()`. The last group may be partial (< 12 months) if the project life doesn't span exact calendar years.

---

## Inflation / Indexation

Standard pattern used across all modules:

```python
factor(year) = start_factor × (1 + inflation_rate)^max(0, year − inflation_start_year)
```

Fields:
- `inflation_rate` — annual rate (default 0.025)
- `inflation_start_year` — first year where compounding applies (default 2025)
- `indexation_start_factor` — multiplier at period 0 (default 1.0)

---

## Module Categories

### Cost of Capital
**WACC_001** — Two-layer WACC separating PPA (lower risk) and merchant (higher risk) revenue streams using the Hamada equation. Outputs are all scalars (no time-series).

### Revenue
**REV_001 / REV_003 (PV / WIND)** — Eight sections: production pass-through, price indexation, spot revenue, 5 PPA slots (inactive slots contribute zero), GoO revenue, production cost tariffs (TSO/DSO/Nord Pool), balancing costs, summary.

**REV_002 (BESS)** — Discharge revenue, charging costs (grid + PV), import/export tariffs, system adjustments, multimarket revenue, summary.

### OPEX
**OPEX_001 (PV)** — 11 components: O&M, commercial management, inverter replacement (post-warranty), land lease (rented + owned), lease tax, insurance, bank guarantee, VE-bonus (% of spot revenue), self-consumption, other.

**OPEX_002 (BESS)** — 4 components: O&M, insurance, trading costs (DKK/MWh discharged), other.

**OPEX_003 (WIND)** — 5 components: O&M service contract, land lease, insurance, grid costs, other.

### CAPEX
**CAPEX_001** — 6 cost buckets (EPC, grid connection, development, land, contingency, other) distributed over the construction period using a user-supplied drawdown profile.

### Debt
**DEBT_001** — Senior debt schedule supporting annuity, straight-line, and bullet repayment. Computes monthly opening/closing balance, drawdown, interest, principal, debt service. DSCR calculated annually if CFADS provided.

### Tax
**TAX_001** — Danish corporate tax. Declining balance depreciation (4 buckets), EBITDA-based interest deduction cap, loss carry-forward (5 years), tax paid in split instalments.

### Financial Statements
**PL_001** — Profit & Loss: EBITDA → EBIT → EBT → net income.

**CF_001** — Cash Flow (indirect): CFO (net income + dep + WC), CFI (capex), CFF (debt + equity + dividends). Opening → closing cash.

**BS_001** — Balance Sheet: fixed assets (gross/accumulated dep/net), cash, total assets = debt + equity (contributed + retained). Includes imbalance diagnostic.

**IRR_001** — DCF valuation: project IRR and equity IRR (bisection on monthly rate), project NPV and equity NPV, payback period, cumulative PFCF, annual PFCF/ECF.

---

## Adding a New Module

1. Create `modules/<category>/XXX_NNN.py` following the anatomy above
2. Add tests in `tests/test_<category>/test_XXX_NNN.py`
3. Add entry to `registry/module_registry.json`
4. If the module has time-series outputs, add rows to `assembly/cell_mapper.py`
5. Wire into `assembly/engine.py` if it depends on or feeds other modules
6. Run `python -m pytest tests/ -q` — all tests must pass
