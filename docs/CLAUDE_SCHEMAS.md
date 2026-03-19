# Schemas, Excel Specs & Assumption DB

Read this file when working on ProjectConfig, Excel output, cell_mapper, or building v2.0 generic modules.

## ProjectConfig (current v1.x — matches engine.py)

```python
class ProjectConfig(BaseModel):
    project_name: str
    market: str = "DK"
    technology: str = "PV"
    timeline: TimelineConfig

    price_curves: Optional[PRICE_CURVES_001.Inputs] = None
    wacc:         Optional[WACC_001.Inputs] = None
    rev_pv:       Optional[REV_001.Inputs] = None
    rev_bess:     Optional[REV_002.Inputs] = None
    rev_wind:     Optional[REV_003.Inputs] = None
    opex_pv:      Optional[OPEX_001.Inputs] = None
    opex_bess:    Optional[OPEX_002.Inputs] = None
    opex_wind:    Optional[OPEX_003.Inputs] = None
    capex:        Optional[CAPEX_001.Inputs] = None
    bess_repow:   Optional[BESS_REPOW_001.Inputs] = None
    constr_finance: Optional[CONSTR_FINANCE_001.Inputs] = None
    debt:         Optional[DEBT_001.Inputs] = None
    debt_sculpt:  Optional[DEBT_SCULPT_001.Inputs] = None
    debt_refi:    Optional[DEBT_REFI_001.Inputs] = None
    debt_linear:  Optional[DEBT_LINEAR_001.Inputs] = None
    shl:          Optional[SHL_001.Inputs] = None
    vat_facility: Optional[VAT_FACILITY_001.Inputs] = None
    dsra:         Optional[DSRA_001.Inputs] = None
    ppa_cfd:      Optional[PPA_CFD_001.Inputs] = None
    repow_debt:   Optional[REPOW_DEBT_001.Inputs] = None
    tax:          Optional[TaxConfig] = None
    tax_de:       Optional[TAX_DE_001.Inputs] = None
    working_capital: Optional[WORKING_CAPITAL_001.Inputs] = None
    sources_uses: Optional[SOURCES_USES_001.Inputs] = None
    valuation:    Optional[VALUATION_001.Inputs] = None
    breakeven:    Optional[BREAKEVEN_001.Inputs] = None
    model_checks: Optional[MODEL_CHECKS_001.Inputs] = None
    dashboard:    Optional[DASHBOARD_001.Inputs] = None
    statements:   StatementConfig = Field(default_factory=StatementConfig)
```

### TaxConfig (user provides config, engine wires time-series)

```python
class TaxConfig(BaseModel):
    country: str = "DK"
    capex_by_bucket: Optional[list[list[float]]] = None  # 4 or 7 lists, length=periods
    opening_balances: list[float] = Field(default_factory=lambda: [0.0] * 7)
```

### StatementConfig

```python
class StatementConfig(BaseModel):
    opening_cash_DKKk: float = 0.0
    opening_contributed_equity_DKKk: float = 0.0
    opening_retained_earnings_DKKk: float = 0.0
    project_discount_rate: Optional[float] = None      # None → use WACC
    equity_discount_rate: Optional[float] = None       # None → use blended CoE
    working_capital_change: list[float] = Field(default_factory=list)
    dividends_paid: list[float] = Field(default_factory=list)
```

### AssemblyResult

```python
@dataclass
class AssemblyResult:
    project_name: str
    periods: int
    start_year: int
    start_month: int
    outputs: dict[str, Any] = field(default_factory=dict)  # module_id → Outputs
    warnings: list[str] = field(default_factory=list)
```

---

## Excel output architecture (format-layer)

### 7-sheet workbook

| Sheet | Modules |
|---|---|
| Cover | Project banner, view selector (1=Bank, 2=IC, 3=Audit), KPI block, color key |
| Revenue | REV_001, REV_002, REV_003, PPA_CFD_001, PRICE_CURVES_001 |
| Costs | OPEX_001–003, CAPEX_001, BESS_REPOW_001, DECOM_PROVISION_001, IMBALANCE_FEE_001 |
| Debt | All DEBT_*, DSRA_001, TAX_001, TAX_DE_001, WACC_001 (scalars in col F) |
| FS_Monthly | PL_001, CF_001, BS_001, IRR_001, WORKING_CAPITAL_001, SOURCES_USES_001, VALUATION_001, BREAKEVEN_001, MODEL_CHECKS_001, DASHBOARD_001 |
| FS_Annual | Same as FS_Monthly — annual aggregation (flows=SUM, stocks=closing balance) |
| Summary | Project metadata + scalar KPIs (IRR, NPV, DSCR, WACC) |

### Column layout (EE Standard — matches PwC/EY reference models)

```
Col  Index  Width   Purpose                          Constant
A-D  1-4    1.3     Narrow indent spacers
E    5      40.5    Row description / label           COL_LABEL
F    6      12.5    Constant / assumption value       COL_CONSTANT
G    7      14.5    Unit                              COL_UNIT
H    8      45.5    Notes (hidden, outline level 1)   COL_NOTES
I    9      45.5    Source (hidden, outline level 1)   COL_SOURCE
J    10     15.5    Total / lifetime average          COL_TOTAL
K    11     2.5     Spacer before time series         COL_SPACER
L+   12+    11.5    Monthly time series               COL_PERIOD_0
```

### Row layout (rows 1-6 = header block, row 7+ = data)

```
Row 1:   Sheet name in col E (bold)
Row 2:   Period end dates (monthly: DD-MMM-YY datetime, annual: year integers)
Row 3:   Phase labels ("Construction" if capex > 0, else "Operations")
Row 4:   Calendar year integers
Row 5:   Column headers (Description, Constant, Unit, Total/avg., period counters)
Row 6:   Blank spacer
Row 7+:  Data rows per ROW_MAP in cell_mapper.py
```

Freeze pane: `L7` on all calculation sheets (locks header rows 1-6 + label cols A-K).

### period_col helper

```python
def period_col(period: int) -> int:
    return COL_PERIOD_0 + period  # period 0 → col L (12)
```

### Col J total logic

```python
# Percentages and ratios: average of non-zero values
if field in _PCT_FIELDS | _RATIO_FIELDS:
    total = sum(non_zero) / len(non_zero) if non_zero else 0.0
# Everything else: lifetime sum
else:
    total = sum(values)
```

### FS_Annual aggregation

```python
# Flows (P&L, CF): SUM of monthly values in each calendar year
# Stocks (BS): closing value of LAST period in each year
BS_CLOSING_FIELDS = {"fixed_assets_gross", "accumulated_depreciation",
    "fixed_assets_net", "cash", "total_assets", "debt_balance",
    "equity", "retained_earnings", "total_liabilities_equity",
    "contributed_equity", "total_equity"}
```

---

## Format standard (excel_formatter.py)

### Colors

| Element | Hex | Usage |
|---|---|---|
| Section header fill | `28837D` | Dark teal — module group labels in ROW_MAP gaps |
| Sub-section label fill | `A6A6A6` | Mid grey — divider rows in SUBSECTION_LABELS |
| Col header fill (row 5) | `44546A` | Slate blue — column header row |
| Phase stripe (row 3) | `F8F8F8` | Near-white — Construction/Operations |
| Total column (col J) | `F2F2F2` | Light grey — on all data rows |
| Input cell fill | `FFF2CC` | Pale yellow — editable assumptions |
| Cover banner | `008080` | Teal — project banner rows 1-3 |
| Cover KPI block | `EBF3FB` | Light blue — KPI background |

### Font colors (F1F9 data flow coding)

| Color | Hex | Meaning |
|---|---|---|
| Black | `000000` | Standard calculation |
| Blue | `0000FF` | Imported from another sheet (gross_revenue, depreciation, etc.) |
| Red | `FF0000` | Exported to another sheet (net_revenue, cfo, closing_cash, etc.) |
| White | `FFFFFF` | On dark fills (headers, section labels) |
| Blue (input) | `0020FF` | Input cell values |

### Borders

| Row type | Border style |
|---|---|
| Subtotals (ebitda, ebit, cfo, etc.) | Thin top + bottom |
| Totals (net_income, closing_cash, etc.) | Medium top, bold label |

### Number formats (owned by formatter — FIELD_FORMATS dict)

```python
FMT_DKKK    = '#,##0'          # DKKk amounts (no decimals)
FMT_PCT     = '0.0%'           # Percentages
FMT_RATIO   = '0.00x'          # DSCR, coverage ratios
FMT_FACTOR  = '0.000'          # Indexation factors, betas
FMT_INTEGER = '#,##0'          # MWh, periods
FMT_DATE    = 'DD-MMM-YY'      # Period end dates
FMT_YEAR    = '0'              # Year integers
```

Font: Calibri 10pt throughout.

### Row grouping

- Detail rows between first/last row of each module section: `outline_level=1`, `hidden=True`
- Minimum 4 rows per section to apply grouping
- Section headers, totals, and subsection labels always visible (outline_level=0)

### Row heights (points)

| Type | Height |
|---|---|
| Banner (rows 1-2) | 22 |
| Sub-banner (row 3) | 18 |
| Year row (row 4) | 15 |
| Col header (row 5) | 20 |
| Section header | 18 |
| Sub-section label | 16 |
| Data (default) | 15 |
| Subtotal | 16 |
| Total | 17 |
| Spacer (empty rows) | 5 |

### Print setup

| Setting | Calc sheets | Cover/Summary |
|---|---|---|
| Orientation | Landscape | Portrait |
| Paper | A3 | A4 |
| Fit to width | 1 page | 1 page |
| Fit to height | Unlimited | 1 page |
| Title rows | 1:6 | — |
| Title cols | A:K | — |
| Margins | L/R 0.5", T/B 0.75" | Default |

### Tab colors

| Sheet | Hex | Category |
|---|---|---|
| Cover | `008080` | Teal — matches banner |
| Revenue, Costs, Debt | `28837D` | Dark teal — calculation |
| FS_Monthly, FS_Annual | `44546A` | Slate — statements |
| Summary | `1F4E79` | Navy — KPI |

---

## Cell mapper (MODULE_SHEET)

```python
MODULE_SHEET = {
    "WACC_001": "Debt", "REV_001": "Revenue", "REV_002": "Revenue",
    "REV_003": "Revenue", "OPEX_001": "Costs", "OPEX_002": "Costs",
    "OPEX_003": "Costs", "CAPEX_001": "Costs", "BESS_REPOW_001": "Costs",
    "DEBT_001": "Debt", "DEBT_SCULPT_001": "Debt", "SHL_001": "Debt",
    "VAT_FACILITY_001": "Debt", "DSRA_001": "Debt", "DEBT_REFI_001": "Debt",
    "DEBT_LINEAR_001": "Debt", "CONSTR_FINANCE_001": "Debt",
    "REPOW_DEBT_001": "Debt", "TAX_001": "Debt", "TAX_DE_001": "Debt",
    "PL_001": "Statements", "CF_001": "Statements", "BS_001": "Statements",
    "IRR_001": "Statements", "PRICE_CURVES_001": "Revenue",
    "WORKING_CAPITAL_001": "Statements", "PPA_CFD_001": "Revenue",
    "SOURCES_USES_001": "Statements", "MODEL_CHECKS_001": "Statements",
    "DASHBOARD_001": "Statements", "VALUATION_001": "Statements",
    "BREAKEVEN_001": "Statements",
}
```

Note: ROW_MAP uses `"Statements"` as the sheet key. `excel_writer.py` maps this to `"FS_Monthly"` when writing. `FS_Annual` reuses the same ROW_MAP rows with annual aggregation.

### SUBSECTION_LABELS

Pre-planned grey divider rows in gaps between module sections. Defined in `cell_mapper.py` as `SUBSECTION_LABELS: dict[tuple[str, int], str]`. Row numbers verified conflict-free against ROW_MAP. FS_Monthly and FS_Annual share identical row numbers.

### SECTION_HEADER_ROWS

Teal section headers in gaps between module groups. Defined in `excel_formatter.py`. Row numbers verified conflict-free against ROW_MAP.

---

## Assumption database structure

Located in `registry/assumption_db/XX.json`. Per-market files.

```json
{
  "_meta": { "market": "Denmark", "currency": "DKK", "last_updated": "2026-03",
             "sources": ["DEA", "Energinet", "BNEF", "EE internal"] },
  "cost_of_capital": { ... },
  "pv_assumptions": { "p50_yield_mwh_per_mwp": 1050, "degradation": 0.0025 },
  "capex": { "pv_dkk_per_mwp": 4200000 },
  "debt": { "typical_tenor_years": 18, "typical_rate": 0.045 },
  "tax": { "cit_rate": 0.22, "depreciation_rate": 0.25 },
  "opex_templates": {
    "pv_standard": [
      {"cost_type": "fixed", "name": "O&M", "annual_DKKk": 85, "unit": "per_mwp", "_confidence": "yellow"},
      {"cost_type": "production_linked", "name": "Balancing", "rate_DKK_per_MWh": 3.5, "_confidence": "yellow"},
      {"cost_type": "scheduled", "name": "Inverter replacement", "events": [[144, 5000]], "_confidence": "yellow"}
    ]
  }
}
```

Confidence: `"green"` = official source, `"yellow"` = EE benchmark, `"red"` = user must provide.

---

## v2.0 TARGET: Generic DEBT module

**Does not exist in code yet. Use only when Martin says "migrate."**

```python
class FacilityConfig(BaseModel):
    name: str
    active: bool = True
    facility_DKKk: float
    type: Literal["linear", "annuity", "bullet", "sculpted", "revolving"]
    start_period: int
    grace_periods: int = 0
    tenor_months: int
    repayments_per_year: int = 2
    margin: float
    base_rate: float = 0.0
    hedge_pct: float = 0.0
    swap_rate: float | None = None
    arrangement_fee_pct: float = 0.0
    commitment_fee_rate: float = 0.0
    drawdowns: list[float]
    equity_first: bool = True
    subordinated: bool = False
    accrued: bool = False                              # PIK
    dscr_streams: list[DSCRStream] | None = None       # sculpted only
    leverage_cap_pct: float | None = None
    revolving_inflows: list[float] | None = None       # revolving only
    reimbursement_delay_months: int = 0
    replaces_facility: str | None = None               # refi only
    refi_period: int | None = None

    @model_validator(mode="after")
    def _validate_type_specific(self):
        if self.type == "sculpted" and not self.dscr_streams:
            raise ValueError(f"'{self.name}': sculpted requires dscr_streams")
        if self.type == "revolving" and self.revolving_inflows is None:
            raise ValueError(f"'{self.name}': revolving requires revolving_inflows")
        if self.replaces_facility and self.refi_period is None:
            raise ValueError(f"'{self.name}': replaces_facility requires refi_period")
        if self.accrued and not self.subordinated:
            raise ValueError(f"'{self.name}': accrued (PIK) only for subordinated")
        return self
```

## v2.0 TARGET: Generic OPEX module

**Does not exist in code yet.**

```python
OpexItem = FixedCost | ProductionLinkedCost | ScheduledReplacement | DerivedCost
# Each sub-type has cost_type Literal discriminator
# Standard item sets per market/tech in assumption_db → opex_templates
```

## v2.0 TARGET: Generic RESERVE_ACCOUNT

**Does not exist in code yet. Currently DSRA_001 handles this.**

```python
class ReserveConfig(BaseModel):
    name: str
    sizing_method: Literal["months_forward", "fixed", "pct_of_debt"]
    months_forward: int = 6
    reference_series: list[float] | None = None
    release_at_maturity: bool = True
```

---

## Contribution template

File: `contributions/TEMPLATE.md`

```
# Contribution: [SHORT NAME]
## Source: Model, Sheet, Rows, Market
## Category: [ ] New OPEX component / debt module / tax / revenue / market / extension
## Specification: [calculation, inputs, outputs, formulas]
## Why existing modules can't cover this
## Proposed implementation: [file, complexity, dependencies]
## Test cases: [2+ known input→output pairs]
```
