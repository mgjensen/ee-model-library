"""
assembly/engine.py

Orchestration engine for ee-model-library.

Runs all enabled calculation modules in dependency order, wiring upstream
outputs into downstream module inputs automatically.

Execution order:
    1. WACC_001   — cost of capital (scalar outputs)
    2. REV_001    — PV revenue
    3. REV_002    — BESS revenue
    4. REV_003    — Wind revenue
    5. OPEX_001   — PV OPEX
    6. OPEX_002   — BESS OPEX
    7. OPEX_003   — Wind OPEX
    8. CAPEX_001  — capital expenditure
    9. DEBT_001   — debt schedule
   10. TAX_001    — Danish corporate tax  (wired: ebitda, interest, capex_by_bucket)
   11. PL_001     — P&L statement         (fully wired)
   12. CF_001     — cash flow statement   (fully wired)
   13. BS_001     — balance sheet         (fully wired)
   14. IRR_001    — DCF valuation         (fully wired)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator as _model_validator

import modules.market.PRICE_CURVES_001 as PRICE_CURVES_001
import modules.core.WACC_001 as WACC_001
import modules.revenue.REV_001 as REV_001
import modules.revenue.REV_002 as REV_002
import modules.revenue.REV_003 as REV_003
import modules.costs.OPEX_001 as OPEX_001
import modules.costs.OPEX_002 as OPEX_002
import modules.costs.OPEX_003 as OPEX_003
import modules.capex.CAPEX_001 as CAPEX_001
import modules.capex.BESS_REPOW_001 as BESS_REPOW_001
import modules.debt.CONSTR_FINANCE_001 as CONSTR_FINANCE_001
import modules.debt.DEBT_001 as DEBT_001
import modules.debt.DEBT_SCULPT_001 as DEBT_SCULPT_001
import modules.debt.SHL_001 as SHL_001
import modules.debt.VAT_FACILITY_001 as VAT_FACILITY_001
import modules.debt.DEBT_REFI_001 as DEBT_REFI_001
import modules.debt.DEBT_LINEAR_001 as DEBT_LINEAR_001
import modules.debt.DSRA_001 as DSRA_001
import modules.tax.TAX_001 as TAX_001
import modules.tax.TAX_DE_001 as TAX_DE_001
import modules.statements.PL_001 as PL_001
import modules.statements.CF_001 as CF_001
import modules.statements.BS_001 as BS_001
import modules.statements.IRR_001 as IRR_001
import modules.statements.VALUATION_001 as VALUATION_001
import modules.statements.BREAKEVEN_001 as BREAKEVEN_001
import modules.debt.REPOW_DEBT_001 as REPOW_DEBT_001
import modules.debt.CASH_SWEEP_001 as CASH_SWEEP_001
import modules.debt.BRIDGE_FACILITY_001 as BRIDGE_FACILITY_001
import modules.debt.MRA_001 as MRA_001
import modules.costs.DECOM_PROVISION_001 as DECOM_PROVISION_001
import modules.costs.IMBALANCE_FEE_001 as IMBALANCE_FEE_001
import modules.tax.TAX_LT_001 as TAX_LT_001
import modules.tax.TAX_AU_001 as TAX_AU_001
import modules.statements.WORKING_CAPITAL_001 as WORKING_CAPITAL_001
import modules.statements.SOURCES_USES_001 as SOURCES_USES_001
import modules.revenue.PPA_CFD_001 as PPA_CFD_001
import modules.checks.MODEL_CHECKS_001 as MODEL_CHECKS_001
import modules.reporting.DASHBOARD_001 as DASHBOARD_001
import modules.statements.DIV_001 as DIV_001


# ============================================================================
# CONFIG MODELS
# ============================================================================

class TimelineConfig(BaseModel):
    """Project timeline — defined once, injected into every module."""
    periods: int = Field(..., gt=0, description="Total project life in months")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")
    currency: str = Field("DKK", description="Project currency: DKK, EUR, USD, AUD")
    currency_label: str = Field("kEUR", description="Currency unit label for Excel output (e.g. kEUR, DKKk, kAUD)")


class TaxConfig(BaseModel):
    """
    User-supplied TAX_001 inputs.
    ebitda and total_interest are wired by the engine from upstream outputs.
    """
    country: str = Field("DK", description="Market — used to load assumption_db")
    capex_by_bucket: Optional[list[list[float]]] = Field(
        None,
        description=(
            "Monthly capex additions per bucket — 4 or 7 lists each length=periods. "
            "None → engine auto-populates bucket 0 from CAPEX_001.total_capex_monthly."
        ),
    )
    opening_balances: list[float] = Field(
        default_factory=lambda: [0.0] * 7,
        description="Opening tax depreciation basis per bucket DKKk (4 or 7 values)"
    )


class StatementConfig(BaseModel):
    """
    User-supplied inputs for the statement modules (PL, CF, BS, IRR).
    All time-series inputs to these modules are wired by the engine from upstream outputs.
    """
    opening_cash_DKKk: float = Field(0.0, description="Opening cash balance DKKk")
    opening_contributed_equity_DKKk: float = Field(
        0.0, description="Equity contributed at financial close DKKk"
    )
    opening_retained_earnings_DKKk: float = Field(
        0.0, description="Retained earnings at project start DKKk"
    )
    project_discount_rate: Optional[float] = Field(
        None, description="Annual project discount rate for NPV. None → use WACC_001.wacc"
    )
    equity_discount_rate: Optional[float] = Field(
        None,
        description="Annual equity discount rate for NPV. None → use WACC_001.blended_cost_of_equity"
    )
    working_capital_change: list[float] = Field(
        default_factory=list,
        description="Monthly working capital change DKKk (optional)"
    )
    dividends_paid: list[float] = Field(
        default_factory=list,
        description="Monthly dividends paid DKKk (optional)"
    )

    # Retrofit opening balances for BS_001 (projects with prior operating history)
    opening_fixed_assets_gross_DKKk: float = Field(
        0.0, description="Gross fixed assets at model start from prior operations"
    )
    opening_accumulated_depreciation_DKKk: float = Field(
        0.0, le=0, description="Accumulated depreciation at model start (negative)"
    )
    opening_debt_balance_DKKk: float = Field(
        0.0, description="Debt balance carried in at model start"
    )


class SPVStructure(str, Enum):
    SINGLE = "single_spv"   # One entity, debt allocated pro-rata
    DUAL = "dual_spv"       # Separate entities (Phase 2: NotImplementedError)


class TechEntity(BaseModel):
    """One technology within a hybrid project (split-tech mode)."""
    tech_id: str = Field(..., description="Prefix for outputs: 'PV', 'BESS', 'WIND'")
    technology: str = Field(..., description="Module type: 'PV', 'BESS', 'WIND'")
    cod_period: int = Field(0, ge=0, description="0-based period of COD for this tech")
    asset_life_years: int = Field(30, gt=0)

    # Module configs — same types as top-level ProjectConfig
    rev: Optional[Any] = None
    opex: Optional[Any] = None
    capex: Optional[Any] = None
    bess_repow: Optional[Any] = None
    constr_finance: Optional[Any] = None
    tax_config: Optional[Any] = None
    independent_tax: bool = True


class ProjectConfig(BaseModel):
    """
    Top-level project configuration.

    Each module config field accepts the module's own Inputs model.
    Set to None to disable a module — disabled modules are skipped and
    their outputs are absent from AssemblyResult.

    The engine overrides periods / start_year / start_month from `timeline`
    before calling each module's calculate(), so these fields in sub-configs
    are ignored.

    Split-tech mode: set `tech_entities` to a list of TechEntity objects.
    The engine runs independent pipelines per technology and consolidates.
    """
    project_name: str
    market: str = "DK"
    technology: str = "PV"
    timeline: TimelineConfig

    # Split-tech mode
    tech_entities: Optional[list[TechEntity]] = None
    spv_structure: SPVStructure = SPVStructure.SINGLE

    # Module configs — None = disabled
    price_curves: Optional[PRICE_CURVES_001.Inputs] = None  # PRICE_CURVES_001
    wacc:     Optional[WACC_001.Inputs]  = None
    rev_pv:   Optional[REV_001.Inputs]   = None   # REV_001
    rev_bess: Optional[REV_002.Inputs]   = None   # REV_002
    rev_wind: Optional[REV_003.Inputs]   = None   # REV_003
    opex_pv:  Optional[OPEX_001.Inputs]  = None   # OPEX_001
    opex_bess: Optional[OPEX_002.Inputs] = None   # OPEX_002
    opex_wind: Optional[OPEX_003.Inputs] = None   # OPEX_003
    capex:    Optional[CAPEX_001.Inputs] = None   # CAPEX_001
    bess_repow: Optional[BESS_REPOW_001.Inputs] = None  # BESS_REPOW_001
    constr_finance: Optional[CONSTR_FINANCE_001.Inputs] = None  # CONSTR_FINANCE_001
    debt:     Optional[DEBT_001.Inputs]  = None   # DEBT_001
    debt_sculpt: Optional[DEBT_SCULPT_001.Inputs] = None  # DEBT_SCULPT_001
    debt_refi: Optional[DEBT_REFI_001.Inputs] = None  # DEBT_REFI_001
    debt_linear: Optional[DEBT_LINEAR_001.Inputs] = None  # DEBT_LINEAR_001
    shl:      Optional[SHL_001.Inputs]   = None   # SHL_001
    vat_facility: Optional[VAT_FACILITY_001.Inputs] = None  # VAT_FACILITY_001
    dsra: Optional[DSRA_001.Inputs] = None  # DSRA_001
    ppa_cfd:  Optional[PPA_CFD_001.Inputs] = None  # PPA_CFD_001
    repow_debt: Optional[REPOW_DEBT_001.Inputs] = None  # REPOW_DEBT_001
    bridge: Optional[BRIDGE_FACILITY_001.Inputs] = None  # BRIDGE_FACILITY_001
    cash_sweep_module: Optional[CASH_SWEEP_001.Inputs] = None  # CASH_SWEEP_001
    mra: Optional[MRA_001.Inputs] = None  # MRA_001
    decom: Optional[DECOM_PROVISION_001.Inputs] = None  # DECOM_PROVISION_001
    imbalance_fee: Optional[IMBALANCE_FEE_001.Inputs] = None  # IMBALANCE_FEE_001
    tax_lt: Optional[TAX_LT_001.Inputs] = None  # TAX_LT_001
    tax_au: Optional[TAX_AU_001.Inputs] = None  # TAX_AU_001
    tax:      Optional[TaxConfig]        = None   # TAX_001
    tax_de:   Optional[TAX_DE_001.Inputs] = None  # TAX_DE_001
    working_capital: Optional[WORKING_CAPITAL_001.Inputs] = None  # WORKING_CAPITAL_001
    sources_uses: Optional[SOURCES_USES_001.Inputs] = None  # SOURCES_USES_001
    valuation: Optional[VALUATION_001.Inputs] = None  # VALUATION_001
    breakeven: Optional[BREAKEVEN_001.Inputs] = None  # BREAKEVEN_001
    model_checks: Optional[MODEL_CHECKS_001.Inputs] = None  # MODEL_CHECKS_001
    dashboard: Optional[DASHBOARD_001.Inputs] = None  # DASHBOARD_001
    div: Optional[DIV_001.Inputs] = None  # DIV_001 — dividend distribution
    statements: StatementConfig = Field(default_factory=StatementConfig)

    @_model_validator(mode="after")
    def _validate_split_tech(self):
        if self.tech_entities is not None:
            top_level = [self.rev_pv, self.rev_bess, self.rev_wind,
                         self.opex_pv, self.opex_bess, self.opex_wind]
            if any(v is not None for v in top_level):
                raise ValueError(
                    "Cannot set both tech_entities and top-level rev_pv/rev_bess/"
                    "opex_pv/opex_bess/rev_wind/opex_wind. Use tech_entities for "
                    "split-tech mode or top-level fields for single-pipeline mode."
                )
            if self.spv_structure == SPVStructure.DUAL:
                raise NotImplementedError(
                    "dual_spv mode is not yet implemented. Use single_spv."
                )
        return self


# ============================================================================
# RESULT
# ============================================================================

@dataclass
class AssemblyResult:
    """Container for all module outputs from a single run."""
    project_name: str
    periods: int
    start_year: int
    start_month: int
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def get(self, module_id: str) -> Any:
        """Return output for a given module, or None if disabled."""
        return self.outputs.get(module_id)

    def get_tech(self, tech_id: str, module_id: str) -> Any:
        """Return per-tech output. E.g. result.get_tech('PV', 'PL_001')."""
        return self.outputs.get(f"{tech_id}_{module_id}")


# ============================================================================
# HELPERS
# ============================================================================

def _inject_timeline(inp: BaseModel, tl: TimelineConfig) -> BaseModel:
    """Override periods/start_year/start_month from the project timeline."""
    return inp.model_copy(update={
        "periods": tl.periods,
        "start_year": tl.start_year,
        "start_month": tl.start_month,
    })


def _zeros(n: int) -> list[float]:
    return [0.0] * n


def _add_series(*series: Optional[list[float]], n: int) -> list[float]:
    """Element-wise sum of series; None entries are treated as zero."""
    result = [0.0] * n
    for s in series:
        if s:
            for p in range(n):
                result[p] += s[p]
    return result



# ============================================================================
# SPLIT-TECH HELPERS
# ============================================================================

# Map technology -> (rev_module, rev_module_id, opex_module, opex_module_id)
_TECH_MODULE_MAP = {
    "PV":   (REV_001, "REV_001", OPEX_001, "OPEX_001"),
    "BESS": (REV_002, "REV_002", OPEX_002, "OPEX_002"),
    "WIND": (REV_003, "REV_003", OPEX_003, "OPEX_003"),
}


def _run_per_tech(entity: TechEntity, tl: TimelineConfig, out: dict, result: AssemblyResult) -> None:
    """Run revenue, OPEX, CAPEX, tax, and per-tech financial statements for one TechEntity."""
    n = tl.periods
    tid = entity.tech_id

    # --- Revenue ---
    rev_out = None
    if entity.rev is not None:
        tech_info = _TECH_MODULE_MAP.get(entity.technology)
        if tech_info:
            rev_mod, rev_id = tech_info[0], tech_info[1]
            rev_out = rev_mod.calculate(_inject_timeline(entity.rev, tl))
            out[f"{tid}_{rev_id}"] = rev_out

    # --- OPEX ---
    opex_out = None
    if entity.opex is not None:
        tech_info = _TECH_MODULE_MAP.get(entity.technology)
        if tech_info:
            opex_mod, opex_id = tech_info[2], tech_info[3]
            opex_out = opex_mod.calculate(_inject_timeline(entity.opex, tl))
            out[f"{tid}_{opex_id}"] = opex_out

    # --- CAPEX ---
    capex_out = None
    if entity.capex is not None:
        capex_out = CAPEX_001.calculate(_inject_timeline(entity.capex, tl))
        out[f"{tid}_CAPEX_001"] = capex_out

    # --- BESS Repowering ---
    repow_out = None
    if entity.bess_repow is not None:
        repow_out = BESS_REPOW_001.calculate(_inject_timeline(entity.bess_repow, tl))
        out[f"{tid}_BESS_REPOW_001"] = repow_out

    # --- Construction Finance ---
    if entity.constr_finance is not None:
        cf_inp = entity.constr_finance
        if capex_out and (not cf_inp.capex_monthly or all(c == 0 for c in cf_inp.capex_monthly)):
            cf_inp = cf_inp.model_copy(update={"capex_monthly": capex_out.total_capex_monthly})
        out[f"{tid}_CONSTR_FINANCE_001"] = CONSTR_FINANCE_001.calculate(
            _inject_timeline(cf_inp, tl)
        )

    # --- Per-tech EBITDA ---
    gross_rev = rev_out.net_revenue if rev_out else _zeros(n)
    total_opex = opex_out.total_opex if opex_out else _zeros(n)

    # --- Per-tech Tax ---
    tax_out_tech = None
    if entity.independent_tax and entity.tax_config is not None:
        ebitda_tech = [gross_rev[p] - total_opex[p] for p in range(n)]
        # Determine tax module from market
        # For now: try all in fallback order
        tax_inp = entity.tax_config
        tax_inp = tax_inp.model_copy(update={
            "ebitda": ebitda_tech,
            "interest_expense": _zeros(n),  # per-tech: unlevered (no debt allocation)
        })
        if hasattr(tax_inp, 'capex_by_bucket'):
            if not tax_inp.capex_by_bucket or all(all(v == 0 for v in b) for b in tax_inp.capex_by_bucket):
                if capex_out:
                    cm = capex_out.total_capex_monthly
                    tax_inp = tax_inp.model_copy(update={
                        "capex_by_bucket": [cm] + [_zeros(n) for _ in range(6)]
                    })
        # Try TAX_AU_001, TAX_LT_001, TAX_001 etc.
        try:
            tax_out_tech = TAX_AU_001.calculate(_inject_timeline(tax_inp, tl))
            out[f"{tid}_TAX_AU_001"] = tax_out_tech
        except Exception:
            try:
                tax_out_tech = TAX_001.calculate(_inject_timeline(tax_inp, tl))
                out[f"{tid}_TAX_001"] = tax_out_tech
            except Exception:
                pass

    # --- Per-tech depreciation ---
    dep = tax_out_tech.tax_depreciation if tax_out_tech else _zeros(n)
    if repow_out:
        dep = [dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
    tax_charge = tax_out_tech.tax_charge_accrued if tax_out_tech else _zeros(n)

    # --- Per-tech PL_001 ---
    pl_inputs = PL_001.Inputs(
        periods=n, start_year=tl.start_year, start_month=tl.start_month,
        gross_revenue=gross_rev,
        total_opex=total_opex,
        depreciation=dep,
        interest_expense=_zeros(n),  # per-tech: no debt (Phase 1)
        tax_charge=tax_charge,
    )
    pl_out = PL_001.calculate(pl_inputs)
    out[f"{tid}_PL_001"] = pl_out

    # --- Per-tech CF_001 (unlevered — no debt) ---
    cf_capex = capex_out.total_capex_monthly if capex_out else _zeros(n)
    if repow_out:
        cf_capex = [cf_capex[p] + repow_out.repowering_cost_monthly[p] for p in range(n)]
    cf_inputs = CF_001.Inputs(
        periods=n, start_year=tl.start_year, start_month=tl.start_month,
        net_income=pl_out.net_income,
        depreciation=dep,
        capex_monthly=cf_capex,
        debt_drawdown=[],
        principal_repayment=[],
        interest_paid=_zeros(n),
    )
    cf_out = CF_001.calculate(cf_inputs)
    out[f"{tid}_CF_001"] = cf_out

    # --- Per-tech BS_001 (unlevered) ---
    bs_inputs = BS_001.Inputs(
        periods=n, start_year=tl.start_year, start_month=tl.start_month,
        capex_monthly=cf_capex,
        depreciation_monthly=dep,
        closing_cash=cf_out.closing_cash,
        debt_closing_balance=_zeros(n),
        net_income=pl_out.net_income,
    )
    out[f"{tid}_BS_001"] = BS_001.calculate(bs_inputs)


def _consolidate_split_tech(entities: list[TechEntity], out: dict, tl: TimelineConfig) -> None:
    """Sum per-tech PL/CF/BS arrays into combined outputs at standard keys."""
    n = tl.periods

    # Aggregate PL inputs from per-tech PL outputs
    combined_rev = _zeros(n)
    combined_opex = _zeros(n)
    combined_dep = _zeros(n)
    combined_tax = _zeros(n)

    for entity in entities:
        tid = entity.tech_id
        pl = out.get(f"{tid}_PL_001")
        if pl:
            combined_rev = [combined_rev[p] + pl.gross_revenue[p] for p in range(n)]
            combined_opex = [combined_opex[p] + pl.total_opex[p] for p in range(n)]
            combined_dep = [combined_dep[p] + pl.depreciation[p] for p in range(n)]
            combined_tax = [combined_tax[p] + pl.tax_charge[p] for p in range(n)]

    # Combined PL (interest added later by existing debt wiring)
    out["_split_tech_rev"] = combined_rev
    out["_split_tech_opex"] = combined_opex
    out["_split_tech_dep"] = combined_dep
    out["_split_tech_tax"] = combined_tax


# ============================================================================
# CORE RUN FUNCTION
# ============================================================================

def run(config: ProjectConfig) -> AssemblyResult:
    """
    Execute all enabled modules in dependency order and return AssemblyResult.

    If config.tech_entities is set, runs split-tech mode:
    per-tech pipelines → consolidation → shared modules.
    """
    tl = config.timeline
    n = tl.periods
    result = AssemblyResult(
        project_name=config.project_name,
        periods=n,
        start_year=tl.start_year,
        start_month=tl.start_month,
    )
    out = result.outputs

    # ------------------------------------------------------------------
    # SPLIT-TECH GATE: if tech_entities is set, run per-tech pipelines
    # ------------------------------------------------------------------
    if config.tech_entities is not None:
        # Phase 1: Per-tech revenue, OPEX, CAPEX, tax, PL, CF, BS
        for entity in config.tech_entities:
            _run_per_tech(entity, tl, out, result)
        # Phase 2: Consolidate per-tech into combined arrays
        _consolidate_split_tech(config.tech_entities, out, tl)
        # Fall through to shared modules (WACC, DEBT, combined PL/CF/BS, IRR)
        # The PL wiring below will detect _split_tech_rev and use it

    # ------------------------------------------------------------------
    # Step 0: PRICE_CURVES_001 — market price curves (feeds all modules)
    # ------------------------------------------------------------------
    if config.price_curves is not None:
        out["PRICE_CURVES_001"] = PRICE_CURVES_001.calculate(
            _inject_timeline(config.price_curves, tl)
        )

    # ------------------------------------------------------------------
    # Step 1: WACC_001 (no dependencies — scalar outputs)
    # ------------------------------------------------------------------
    if config.wacc is not None:
        out["WACC_001"] = WACC_001.calculate(config.wacc)

    # ------------------------------------------------------------------
    # Step 2: REV_001 — PV revenue (no dependencies)
    # ------------------------------------------------------------------
    if config.rev_pv is not None:
        out["REV_001"] = REV_001.calculate(_inject_timeline(config.rev_pv, tl))

    # ------------------------------------------------------------------
    # Step 3: REV_002 — BESS revenue (no dependencies)
    # ------------------------------------------------------------------
    if config.rev_bess is not None:
        out["REV_002"] = REV_002.calculate(_inject_timeline(config.rev_bess, tl))

    # ------------------------------------------------------------------
    # Step 4: REV_003 — Wind revenue (no dependencies)
    # ------------------------------------------------------------------
    if config.rev_wind is not None:
        out["REV_003"] = REV_003.calculate(_inject_timeline(config.rev_wind, tl))

    # ------------------------------------------------------------------
    # Step 4.5: PPA_CFD_001 — CfD settlement (no dependencies)
    # ------------------------------------------------------------------
    if config.ppa_cfd is not None:
        out["PPA_CFD_001"] = PPA_CFD_001.calculate(
            _inject_timeline(config.ppa_cfd, tl)
        )

    # ------------------------------------------------------------------
    # Step 5: OPEX_001 — PV OPEX (no dependencies)
    # ------------------------------------------------------------------
    if config.opex_pv is not None:
        out["OPEX_001"] = OPEX_001.calculate(_inject_timeline(config.opex_pv, tl))

    # ------------------------------------------------------------------
    # Step 6: OPEX_002 — BESS OPEX (no dependencies)
    # ------------------------------------------------------------------
    if config.opex_bess is not None:
        out["OPEX_002"] = OPEX_002.calculate(_inject_timeline(config.opex_bess, tl))

    # ------------------------------------------------------------------
    # Step 7: OPEX_003 — Wind OPEX (no dependencies)
    # ------------------------------------------------------------------
    if config.opex_wind is not None:
        out["OPEX_003"] = OPEX_003.calculate(_inject_timeline(config.opex_wind, tl))

    # ------------------------------------------------------------------
    # Step 6: CAPEX_001 — capital expenditure (no dependencies)
    # ------------------------------------------------------------------
    if config.capex is not None:
        out["CAPEX_001"] = CAPEX_001.calculate(_inject_timeline(config.capex, tl))

    # ------------------------------------------------------------------
    # Step 8b: BESS_REPOW_001 — BESS battery stack repowering
    # ------------------------------------------------------------------
    if config.bess_repow is not None:
        out["BESS_REPOW_001"] = BESS_REPOW_001.calculate(
            _inject_timeline(config.bess_repow, tl)
        )

    # ------------------------------------------------------------------
    # Step 9a: CONSTR_FINANCE_001 — construction finance (wired from CAPEX_001)
    # ------------------------------------------------------------------
    if config.constr_finance is not None:
        cf_inp = config.constr_finance
        # Auto-wire capex_monthly from CAPEX_001 if not provided
        if "CAPEX_001" in out and (not cf_inp.capex_monthly or all(c == 0 for c in cf_inp.capex_monthly)):
            cf_inp = cf_inp.model_copy(update={
                "capex_monthly": out["CAPEX_001"].total_capex_monthly,
            })
        out["CONSTR_FINANCE_001"] = CONSTR_FINANCE_001.calculate(
            _inject_timeline(cf_inp, tl)
        )

    # ------------------------------------------------------------------
    # Step 7: DEBT_001 — debt schedule (no dependencies)
    # ------------------------------------------------------------------
    if config.debt is not None:
        out["DEBT_001"] = DEBT_001.calculate(_inject_timeline(config.debt, tl))

    # ------------------------------------------------------------------
    # Step 9b: DEBT_SCULPT_001 — sculpted senior debt (alternative to DEBT_001)
    # ------------------------------------------------------------------
    if config.debt_sculpt is not None:
        out["DEBT_SCULPT_001"] = DEBT_SCULPT_001.calculate(
            _inject_timeline(config.debt_sculpt, tl)
        )

    # ------------------------------------------------------------------
    # Step 9c: DEBT_REFI_001 — sculpted refinancing (wired from DEBT_SCULPT_001)
    # ------------------------------------------------------------------
    if config.debt_refi is not None:
        refi_inp = config.debt_refi
        # Auto-wire original balance from DEBT_SCULPT_001 if available
        sculpt_out = out.get("DEBT_SCULPT_001")
        if sculpt_out and refi_inp.active:
            rp = refi_inp.refi_period
            if rp < n and sculpt_out.closing_balance[rp] > 0.01:
                refi_inp = refi_inp.model_copy(update={
                    "original_balance_at_refi_DKKk": sculpt_out.closing_balance[rp],
                })
        out["DEBT_REFI_001"] = DEBT_REFI_001.calculate(
            _inject_timeline(refi_inp, tl)
        )

    # ------------------------------------------------------------------
    # Step 9d: DEBT_LINEAR_001 — multi-facility linear debt
    # ------------------------------------------------------------------
    if config.debt_linear is not None:
        out["DEBT_LINEAR_001"] = DEBT_LINEAR_001.calculate(
            _inject_timeline(config.debt_linear, tl)
        )

    # ------------------------------------------------------------------
    # Step 9e: REPOW_DEBT_001 — repowering debt facility
    # ------------------------------------------------------------------
    if config.repow_debt is not None:
        out["REPOW_DEBT_001"] = REPOW_DEBT_001.calculate(
            _inject_timeline(config.repow_debt, tl)
        )

    # ------------------------------------------------------------------
    # Step 9.5: SHL_001 — shareholder loan (no dependencies)
    # ------------------------------------------------------------------
    if config.shl is not None:
        out["SHL_001"] = SHL_001.calculate(_inject_timeline(config.shl, tl))

    # ------------------------------------------------------------------
    # Step 9.6: VAT_FACILITY_001 — construction VAT facility
    # ------------------------------------------------------------------
    if config.vat_facility is not None:
        vat_inp = config.vat_facility
        # Auto-wire capex_monthly from CAPEX_001 if not provided
        if not vat_inp.capex_monthly and "CAPEX_001" in out:
            vat_inp = vat_inp.model_copy(update={
                "capex_monthly": out["CAPEX_001"].total_capex_monthly
            })
        out["VAT_FACILITY_001"] = VAT_FACILITY_001.calculate(
            _inject_timeline(vat_inp, tl)
        )

    # ------------------------------------------------------------------
    # Step 9.7: DSRA_001 — debt service reserve (wired: debt_service from DEBT_001)
    # ------------------------------------------------------------------
    if config.dsra is not None:
        dsra_inp = config.dsra
        # Auto-wire debt_service from DEBT_001 or DEBT_SCULPT_001 if not provided
        _debt_dsra = out.get("DEBT_001")
        _sculpt_dsra = out.get("DEBT_SCULPT_001")
        if not dsra_inp.debt_service or all(d == 0 for d in dsra_inp.debt_service):
            if _debt_dsra:
                dsra_inp = dsra_inp.model_copy(update={
                    "debt_service": _debt_dsra.debt_service,
                })
            elif _sculpt_dsra:
                dsra_inp = dsra_inp.model_copy(update={
                    "debt_service": _sculpt_dsra.debt_service,
                })
        out["DSRA_001"] = DSRA_001.calculate(_inject_timeline(dsra_inp, tl))

    # ------------------------------------------------------------------
    # Step 9b: BRIDGE_FACILITY_001 — short-term bridge loan
    # ------------------------------------------------------------------
    if config.bridge is not None:
        out["BRIDGE_FACILITY_001"] = BRIDGE_FACILITY_001.calculate(
            _inject_timeline(config.bridge, tl)
        )

    # ------------------------------------------------------------------
    # Step 9g: MRA_001 — maintenance reserve account
    # ------------------------------------------------------------------
    if config.mra is not None:
        out["MRA_001"] = MRA_001.calculate(_inject_timeline(config.mra, tl))

    # ------------------------------------------------------------------
    # Step 9h: DECOM_PROVISION_001 — decommissioning provision
    # ------------------------------------------------------------------
    if config.decom is not None:
        out["DECOM_PROVISION_001"] = DECOM_PROVISION_001.calculate(
            _inject_timeline(config.decom, tl)
        )

    # ------------------------------------------------------------------
    # Step 9i: CASH_SWEEP_001 — excess cash sweep to debt
    # ------------------------------------------------------------------
    if config.cash_sweep_module is not None:
        out["CASH_SWEEP_001"] = CASH_SWEEP_001.calculate(
            _inject_timeline(config.cash_sweep_module, tl)
        )

    # ------------------------------------------------------------------
    # Step 9j: IMBALANCE_FEE_001 — polluter pays imbalance fee
    # ------------------------------------------------------------------
    if config.imbalance_fee is not None:
        out["IMBALANCE_FEE_001"] = IMBALANCE_FEE_001.calculate(
            _inject_timeline(config.imbalance_fee, tl)
        )

    # ------------------------------------------------------------------
    # Step 8: TAX_001 — tax (wired: ebitda from rev-opex, interest from debt)
    # ------------------------------------------------------------------
    if config.tax is not None:
        debt_out = out.get("DEBT_001")  # None if DEBT_001 not run; interest defaults to zeros

        # Combined net revenue (gross_revenue - costs) from PV + BESS + Wind
        rev_pv_net    = out["REV_001"].net_revenue  if "REV_001"  in out else None
        rev_bess_net  = out["REV_002"].net_revenue  if "REV_002"  in out else None
        rev_wind_net  = out["REV_003"].net_revenue  if "REV_003"  in out else None
        opex_pv_tot   = out["OPEX_001"].total_opex  if "OPEX_001" in out else None
        opex_bess_tot = out["OPEX_002"].total_opex  if "OPEX_002" in out else None
        opex_wind_tot = out["OPEX_003"].total_opex  if "OPEX_003" in out else None

        # EBITDA = combined net revenue - additional OPEX
        gross_rev = _add_series(rev_pv_net, rev_bess_net, rev_wind_net, n=n)
        total_opex_combined = _add_series(opex_pv_tot, opex_bess_tot, opex_wind_tot, n=n)
        ebitda_series = [gross_rev[p] - total_opex_combined[p] for p in range(n)]

        # Interest from DEBT_001
        interest_series = debt_out.interest if debt_out else _zeros(n)

        # capex_by_bucket: use user-supplied or auto-fill bucket 0 from CAPEX_001
        if config.tax.capex_by_bucket is not None:
            capex_by_bucket = config.tax.capex_by_bucket
        elif "CAPEX_001" in out:
            capex_monthly = out["CAPEX_001"].total_capex_monthly
            capex_by_bucket = [capex_monthly] + [_zeros(n) for _ in range(6)]
            # Wire BESS repowering cost into bucket 6
            if "BESS_REPOW_001" in out:
                capex_by_bucket[6] = out["BESS_REPOW_001"].repowering_cost_monthly
        else:
            capex_by_bucket = [_zeros(n) for _ in range(7)]
            result.warnings.append(
                "TAX_001: no CAPEX_001 output and no capex_by_bucket provided — "
                "using zero capex for all depreciation buckets."
            )

        tax_inputs = TAX_001.Inputs(
            country=config.tax.country,
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            ebitda=ebitda_series,
            total_interest=interest_series,
            capex_by_bucket=capex_by_bucket,
            opening_balances=config.tax.opening_balances,
        )
        out["TAX_001"] = TAX_001.calculate(tax_inputs)

    # ------------------------------------------------------------------
    # Step 10b: TAX_DE_001 — German dual-layer tax (alternative to TAX_001)
    # ------------------------------------------------------------------
    if config.tax_de is not None:
        tax_de_inp = config.tax_de
        # Auto-wire ebitda and interest if not fully populated
        tax_de_inp = tax_de_inp.model_copy(update={
            "periods": n,
            "start_year": tl.start_year,
            "start_month": tl.start_month,
        })
        out["TAX_DE_001"] = TAX_DE_001.calculate(tax_de_inp)

    # ------------------------------------------------------------------
    # Step 10c: TAX_LT_001 — Lithuanian tax
    # ------------------------------------------------------------------
    if config.tax_lt is not None:
        tax_lt_inp = config.tax_lt
        # Auto-wire ebitda, interest, and capex from upstream modules
        _rev_pv_net    = out["REV_001"].net_revenue  if "REV_001"  in out else None
        _rev_bess_net  = out["REV_002"].net_revenue  if "REV_002"  in out else None
        _rev_wind_net  = out["REV_003"].net_revenue  if "REV_003"  in out else None
        _opex_pv_tot   = out["OPEX_001"].total_opex  if "OPEX_001" in out else None
        _opex_bess_tot = out["OPEX_002"].total_opex  if "OPEX_002" in out else None
        _opex_wind_tot = out["OPEX_003"].total_opex  if "OPEX_003" in out else None
        _gross_rev_lt = _add_series(_rev_pv_net, _rev_bess_net, _rev_wind_net, n=n)
        _opex_lt = _add_series(_opex_pv_tot, _opex_bess_tot, _opex_wind_tot, n=n)
        _ebitda_lt = [_gross_rev_lt[p] - _opex_lt[p] for p in range(n)]

        # Interest from all debt modules
        _debt_out_lt = out.get("DEBT_001")
        _sculpt_out_lt = out.get("DEBT_SCULPT_001")
        _int_lt = _debt_out_lt.interest if _debt_out_lt else _zeros(n)
        if _sculpt_out_lt:
            _int_lt = [_int_lt[p] + _sculpt_out_lt.interest_accrued[p] for p in range(n)]
        _constr_lt = out.get("CONSTR_FINANCE_001")
        if _constr_lt:
            _int_lt = [_int_lt[p] + _constr_lt.interest[p] for p in range(n)]
        _shl_lt = out.get("SHL_001")
        if _shl_lt:
            _int_lt = [_int_lt[p] + _shl_lt.interest[p] for p in range(n)]

        # Capex buckets
        if not tax_lt_inp.capex_by_bucket or all(
            all(v == 0 for v in b) for b in tax_lt_inp.capex_by_bucket
        ):
            if "CAPEX_001" in out:
                _capex_m = out["CAPEX_001"].total_capex_monthly
                _capex_by_bucket_lt = [_capex_m] + [_zeros(n) for _ in range(6)]
            else:
                _capex_by_bucket_lt = [_zeros(n) for _ in range(7)]
        else:
            _capex_by_bucket_lt = tax_lt_inp.capex_by_bucket

        _update_lt = {
            "ebitda": _ebitda_lt,
            "interest_expense": _int_lt,
            "capex_by_bucket": _capex_by_bucket_lt,
        }
        # Auto-wire SHL interest for SHL deduction limit
        if _shl_lt and tax_lt_inp.shl_interest_limit_active:
            _update_lt["shl_interest_expense"] = list(_shl_lt.interest)
        tax_lt_inp = tax_lt_inp.model_copy(update=_update_lt)
        out["TAX_LT_001"] = TAX_LT_001.calculate(
            _inject_timeline(tax_lt_inp, tl)
        )

    # ------------------------------------------------------------------
    # Step 10d: TAX_AU_001 — Australian tax
    # ------------------------------------------------------------------
    if config.tax_au is not None:
        tax_au_inp = config.tax_au
        # Auto-wire ebitda, interest, capex (same pattern as TAX_LT_001)
        _rev_pv_au    = out["REV_001"].net_revenue  if "REV_001"  in out else None
        _rev_bess_au  = out["REV_002"].net_revenue  if "REV_002"  in out else None
        _rev_wind_au  = out["REV_003"].net_revenue  if "REV_003"  in out else None
        _opex_pv_au   = out["OPEX_001"].total_opex  if "OPEX_001" in out else None
        _opex_bess_au = out["OPEX_002"].total_opex  if "OPEX_002" in out else None
        _opex_wind_au = out["OPEX_003"].total_opex  if "OPEX_003" in out else None
        _gross_rev_au = _add_series(_rev_pv_au, _rev_bess_au, _rev_wind_au, n=n)
        _opex_au = _add_series(_opex_pv_au, _opex_bess_au, _opex_wind_au, n=n)
        _ebitda_au = [_gross_rev_au[p] - _opex_au[p] for p in range(n)]
        _debt_out_au = out.get("DEBT_001")
        _sculpt_out_au = out.get("DEBT_SCULPT_001")
        _int_au = _debt_out_au.interest if _debt_out_au else _zeros(n)
        if _sculpt_out_au:
            _int_au = [_int_au[p] + _sculpt_out_au.interest_accrued[p] for p in range(n)]
        _constr_au = out.get("CONSTR_FINANCE_001")
        if _constr_au:
            _int_au = [_int_au[p] + _constr_au.interest[p] for p in range(n)]
        _shl_au = out.get("SHL_001")
        if _shl_au:
            _int_au = [_int_au[p] + _shl_au.interest[p] for p in range(n)]
        if not tax_au_inp.capex_by_bucket or all(
            all(v == 0 for v in b) for b in tax_au_inp.capex_by_bucket
        ):
            if "CAPEX_001" in out:
                _capex_au = out["CAPEX_001"].total_capex_monthly
                _capex_by_bucket_au = [_capex_au] + [_zeros(n) for _ in range(6)]
            else:
                _capex_by_bucket_au = [_zeros(n) for _ in range(7)]
        else:
            _capex_by_bucket_au = tax_au_inp.capex_by_bucket
        tax_au_inp = tax_au_inp.model_copy(update={
            "ebitda": _ebitda_au,
            "interest_expense": _int_au,
            "capex_by_bucket": _capex_by_bucket_au,
        })
        out["TAX_AU_001"] = TAX_AU_001.calculate(
            _inject_timeline(tax_au_inp, tl)
        )

    # ------------------------------------------------------------------
    # Step 9: PL_001 — P&L (fully wired)
    # ------------------------------------------------------------------
    _split_tech_active = "_split_tech_rev" in out
    if _split_tech_active or any(k in out for k in ("TAX_001", "TAX_DE_001", "TAX_LT_001", "TAX_AU_001", "DEBT_001", "DEBT_SCULPT_001", "REV_001", "REV_002", "REV_003")):
        if _split_tech_active:
            # Split-tech mode: use consolidated arrays from per-tech pipelines
            gross_rev = out["_split_tech_rev"]
            total_opex_combined = out["_split_tech_opex"]
        else:
            rev_pv_net    = out["REV_001"].net_revenue  if "REV_001"  in out else None
            rev_bess_net  = out["REV_002"].net_revenue  if "REV_002"  in out else None
            rev_wind_net  = out["REV_003"].net_revenue  if "REV_003"  in out else None
            opex_pv_tot   = out["OPEX_001"].total_opex  if "OPEX_001" in out else None
            opex_bess_tot = out["OPEX_002"].total_opex  if "OPEX_002" in out else None
            opex_wind_tot = out["OPEX_003"].total_opex  if "OPEX_003" in out else None
            gross_rev = _add_series(rev_pv_net, rev_bess_net, rev_wind_net, n=n)
            total_opex_combined = _add_series(opex_pv_tot, opex_bess_tot, opex_wind_tot, n=n)
        tax_out = out.get("TAX_001")
        tax_de_out = out.get("TAX_DE_001")
        tax_lt_out = out.get("TAX_LT_001")
        tax_au_out = out.get("TAX_AU_001")
        debt_out = out.get("DEBT_001")
        sculpt_out = out.get("DEBT_SCULPT_001")
        _tax_any = tax_out or tax_de_out or tax_lt_out or tax_au_out
        if _split_tech_active:
            dep = out["_split_tech_dep"]
        else:
            dep = _tax_any.tax_depreciation if _tax_any else _zeros(n)
            repow_out = out.get("BESS_REPOW_001")
            if repow_out:
                dep = [dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
        interest = debt_out.interest if debt_out else _zeros(n)
        # Add sculpted debt interest (accrued = expense for PL)
        if sculpt_out:
            interest = [interest[p] + sculpt_out.interest_accrued[p] for p in range(n)]
        # Add refi interest
        refi_out = out.get("DEBT_REFI_001")
        if refi_out:
            interest = [interest[p] + refi_out.interest[p] for p in range(n)]
        # Add linear debt interest
        linear_out = out.get("DEBT_LINEAR_001")
        if linear_out:
            interest = [interest[p] + linear_out.total_interest[p] for p in range(n)]
        # IDC split: pre-COD interest is capitalised, post-COD is expensed in PL
        _cod = (config.constr_finance.cod_period if config.constr_finance
                else (config.capex.construction_start_period + config.capex.construction_periods
                      if config.capex else 0))

        # Construction finance interest + commitment fee — post-COD only in PL
        # (pre-COD interest capitalised as IDC; commitment fee always expensed)
        constr_out = out.get("CONSTR_FINANCE_001")
        if constr_out:
            interest = [interest[p]
                        + (constr_out.interest[p] if p >= _cod else 0.0)
                        + constr_out.commitment_fee[p]
                        for p in range(n)]
        # SHL interest — post-COD only in PL (pre-COD capitalised as IDC)
        shl_out = out.get("SHL_001")
        if shl_out:
            interest = [interest[p] + (shl_out.interest[p] if p >= _cod else 0.0)
                        for p in range(n)]
        if _split_tech_active:
            tax_charge = out["_split_tech_tax"]
        else:
            tax_charge = _tax_any.tax_charge_accrued if _tax_any else _zeros(n)

        pl_inputs = PL_001.Inputs(
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            gross_revenue=gross_rev,
            total_opex=total_opex_combined,
            depreciation=dep,
            interest_expense=interest,
            tax_charge=tax_charge,
        )
        out["PL_001"] = PL_001.calculate(pl_inputs)

    # ------------------------------------------------------------------
    # Step 10: CF_001 — cash flow (fully wired)
    # ------------------------------------------------------------------
    if "PL_001" in out:
        pl_out = out["PL_001"]
        _tax_any_cf = out.get("TAX_001") or out.get("TAX_DE_001") or out.get("TAX_LT_001") or out.get("TAX_AU_001")
        debt_out = out.get("DEBT_001")
        sculpt_out = out.get("DEBT_SCULPT_001")
        capex_out = out.get("CAPEX_001")
        sc = config.statements

        repow_out = out.get("BESS_REPOW_001")
        cf_dep = _tax_any_cf.tax_depreciation if _tax_any_cf else _zeros(n)
        if repow_out:
            cf_dep = [cf_dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
        cf_capex = capex_out.total_capex_monthly if capex_out else _zeros(n)
        if repow_out:
            cf_capex = [cf_capex[p] + repow_out.repowering_cost_monthly[p] for p in range(n)]

        cf_draw = debt_out.drawdown if debt_out else []
        cf_princ = debt_out.principal if debt_out else []
        cf_int = debt_out.interest if debt_out else _zeros(n)
        # Sculpted debt cash flows
        if sculpt_out:
            cf_draw = _add_series(cf_draw or None, sculpt_out.drawdown, n=n)
            cf_princ = _add_series(cf_princ or None, sculpt_out.principal, n=n)
            cf_int = [cf_int[p] + sculpt_out.interest_paid[p] for p in range(n)]
        refi_out = out.get("DEBT_REFI_001")
        if refi_out:
            cf_draw = _add_series(cf_draw or None, refi_out.drawdown, n=n)
            cf_princ = _add_series(cf_princ or None, refi_out.principal, n=n)
            cf_int = [cf_int[p] + refi_out.interest[p] for p in range(n)]
        linear_out = out.get("DEBT_LINEAR_001")
        if linear_out:
            cf_draw = _add_series(cf_draw or None, linear_out.total_drawdown, n=n)
            cf_princ = _add_series(cf_princ or None, linear_out.total_principal, n=n)
            cf_int = [cf_int[p] + linear_out.total_interest[p] for p in range(n)]
        constr_out = out.get("CONSTR_FINANCE_001")
        if constr_out:
            cf_draw = _add_series(cf_draw or None, constr_out.drawdown, n=n)
            cf_princ = _add_series(cf_princ or None, constr_out.repayment, n=n)
            cf_int = [cf_int[p] + constr_out.interest[p] + constr_out.commitment_fee[p]
                      for p in range(n)]

        # SHL wiring into CF
        shl_out = out.get("SHL_001")
        if shl_out:
            _cod = (config.constr_finance.cod_period if config.constr_finance
                    else (config.capex.construction_start_period + config.capex.construction_periods
                          if config.capex else 0))
            # Post-COD PIK interest: charged in PL but not paid → add back as non-cash
            shl_pik = [
                (shl_out.interest[p] - shl_out.interest_cash[p]) if p >= _cod else 0.0
                for p in range(n)
            ]
            cf_dep = [cf_dep[p] + shl_pik[p] for p in range(n)]
            # SHL drawdowns: opening balance at each period minus previous closing
            # represents new equity-funded SHL disbursements
            shl_draw = [0.0] * n
            shl_draw[0] = shl_out.opening_balance[0]  # initial sizing appears at P0
            for p in range(1, n):
                new_shl = shl_out.opening_balance[p] - shl_out.closing_balance[p - 1]
                if new_shl > 0.001:
                    shl_draw[p] = new_shl
            cf_draw = _add_series(cf_draw or None, shl_draw, n=n)
            # Pure equity portion (non-SHL): flows through CFF as equity injection
            if config.shl and config.shl.equity_contributed:
                shl_pct = config.shl.shl_pct_of_equity
                pure_eq = [(1.0 - shl_pct) * config.shl.equity_contributed[p] for p in range(n)]
                cf_draw = _add_series(cf_draw or None, pure_eq, n=n)
            # Cash interest (0 for PIK mode) flows through CFF interest_paid
            cf_int = [cf_int[p] + shl_out.interest_cash[p] for p in range(n)]
            # SHL repayments flow through CFF principal
            cf_princ = _add_series(cf_princ or None, shl_out.repayment, n=n)

        cf_inputs = CF_001.Inputs(
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            opening_cash_DKKk=sc.opening_cash_DKKk,
            net_income=pl_out.net_income,
            depreciation=cf_dep,
            working_capital_change=sc.working_capital_change or [],
            capex_monthly=cf_capex,
            debt_drawdown=cf_draw,
            principal_repayment=cf_princ,
            interest_paid=cf_int,
            dividends_paid=sc.dividends_paid or [],
        )
        out["CF_001"] = CF_001.calculate(cf_inputs)

    # ------------------------------------------------------------------
    # Step 10.5: DIV_001 — dividend distribution (after CF_001, before BS_001)
    # ------------------------------------------------------------------
    dividends_computed: list[float] = []
    if "CF_001" in out and "PL_001" in out and config.div is not None:
        cf_out = out["CF_001"]
        pl_out = out["PL_001"]
        sc = config.statements
        _cod_div = (config.constr_finance.cod_period if config.constr_finance
                    else (config.capex.construction_start_period + config.capex.construction_periods
                          if config.capex else 0))

        # Adjust net_income for SHL PIK interest: non-cash charge should not
        # suppress distributable profits (PIK is already added back in CF)
        div_net_income = list(pl_out.net_income)
        _shl_div = out.get("SHL_001")
        if _shl_div:
            for p in range(n):
                pik = _shl_div.interest[p] - _shl_div.interest_cash[p]
                if p >= _cod_div:
                    div_net_income[p] += pik

        div_inputs = DIV_001.Inputs(
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            cod_period=_cod_div,
            net_income=div_net_income,
            closing_cash=cf_out.closing_cash,
            opening_retained_earnings=sc.opening_retained_earnings_DKKk,
            opening_contributed_equity=sc.opening_contributed_equity_DKKk,
            payout_ratio=config.div.payout_ratio,
            cash_reserve=config.div.cash_reserve,
            payment_months=config.div.payment_months,
            capital_reduction_active=config.div.capital_reduction_active,
            capital_reduction_threshold=config.div.capital_reduction_threshold,
        )
        if config.div.ops_end_period is not None:
            div_inputs = div_inputs.model_copy(update={"ops_end_period": config.div.ops_end_period})

        out["DIV_001"] = DIV_001.calculate(div_inputs)
        dividends_computed = out["DIV_001"].dividends_paid

        # Re-run CF_001 with computed dividends
        cf_inputs_with_div = cf_inputs.model_copy(update={
            "dividends_paid": dividends_computed,
        })
        out["CF_001"] = CF_001.calculate(cf_inputs_with_div)

    # ------------------------------------------------------------------
    # Step 11: BS_001 — balance sheet (fully wired)
    # ------------------------------------------------------------------
    if "CF_001" in out:
        cf_out = out["CF_001"]
        pl_out = out["PL_001"]
        _tax_any_bs = out.get("TAX_001") or out.get("TAX_DE_001") or out.get("TAX_LT_001") or out.get("TAX_AU_001")
        debt_out = out.get("DEBT_001")
        capex_out = out.get("CAPEX_001")
        sc = config.statements

        repow_out = out.get("BESS_REPOW_001")
        bs_capex = capex_out.total_capex_monthly if capex_out else _zeros(n)
        bs_dep = _tax_any_bs.tax_depreciation if _tax_any_bs else _zeros(n)
        if repow_out:
            bs_capex = [bs_capex[p] + repow_out.repowering_cost_monthly[p] for p in range(n)]
            bs_dep = [bs_dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
        # Capitalise pre-COD interest as IDC (interest during construction)
        _cod = (config.constr_finance.cod_period if config.constr_finance
                else (config.capex.construction_start_period + config.capex.construction_periods
                      if config.capex else 0))
        # SHL pre-COD interest → capitalised
        shl_out = out.get("SHL_001")
        if shl_out:
            bs_capex = [bs_capex[p] + (shl_out.interest[p] if p < _cod else 0.0)
                        for p in range(n)]
        # Construction finance pre-COD interest → capitalised
        constr_out = out.get("CONSTR_FINANCE_001")
        if constr_out:
            bs_capex = [bs_capex[p] + (constr_out.interest[p] if p < _cod else 0.0)
                        for p in range(n)]

        bs_kwargs: dict = dict(
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            opening_contributed_equity_DKKk=sc.opening_contributed_equity_DKKk,
            opening_retained_earnings_DKKk=sc.opening_retained_earnings_DKKk,
            opening_fixed_assets_gross_DKKk=sc.opening_fixed_assets_gross_DKKk,
            opening_accumulated_depreciation_DKKk=sc.opening_accumulated_depreciation_DKKk,
            opening_debt_balance_DKKk=sc.opening_debt_balance_DKKk,
            capex_monthly=bs_capex,
            depreciation_monthly=bs_dep,
            closing_cash=cf_out.closing_cash,
            debt_closing_balance=_add_series(
                debt_out.closing_balance if debt_out else None,
                out["DEBT_SCULPT_001"].closing_balance if "DEBT_SCULPT_001" in out else None,
                out["DEBT_REFI_001"].closing_balance if "DEBT_REFI_001" in out else None,
                out["DEBT_LINEAR_001"].total_closing_balance if "DEBT_LINEAR_001" in out else None,
                out["CONSTR_FINANCE_001"].closing_balance if "CONSTR_FINANCE_001" in out else None,
                out["SHL_001"].closing_balance if "SHL_001" in out else None,
                n=n,
            ),
            net_income=pl_out.net_income,
        )
        # Equity contributions as time series — pure equity only (not SHL)
        # SHL is already on the BS as debt (via debt_closing_balance)
        shl_out = out.get("SHL_001")
        if shl_out and config.shl and config.shl.equity_contributed:
            shl_pct = config.shl.shl_pct_of_equity
            eq_pure = [(1.0 - shl_pct) * config.shl.equity_contributed[p] for p in range(n)]
            bs_kwargs["equity_contributions"] = eq_pure

        if dividends_computed:
            bs_kwargs["dividends_paid"] = dividends_computed
        elif sc.dividends_paid:
            bs_kwargs["dividends_paid"] = sc.dividends_paid
        out["BS_001"] = BS_001.calculate(BS_001.Inputs(**bs_kwargs))

    # ------------------------------------------------------------------
    # Step 12: IRR_001 — DCF valuation (fully wired)
    # ------------------------------------------------------------------
    if "CF_001" in out:
        cf_out = out["CF_001"]
        wacc_out = out.get("WACC_001")
        sc = config.statements

        pfcf = [cf_out.cfo[p] + cf_out.cfi[p] for p in range(n)]
        # PFCF = CFO + CFI is levered (includes cash interest via net_income).
        # For unlevered Project IRR, add back cash interest only.
        # PIK interest is already neutral in CFO (deducted in NI, added back as
        # non-cash in depreciation), so only cash interest needs to be restored.
        pl_out_irr = out.get("PL_001")
        if pl_out_irr:
            _shl_irr = out.get("SHL_001")
            _cod_irr = (config.constr_finance.cod_period if config.constr_finance
                        else (config.capex.construction_start_period + config.capex.construction_periods
                              if config.capex else 0))
            for p in range(n):
                addback = pl_out_irr.interest_expense[p]
                # Subtract SHL PIK (already neutralised in CFO via non-cash addback)
                if _shl_irr and p >= _cod_irr:
                    pik = _shl_irr.interest[p] - _shl_irr.interest_cash[p]
                    addback -= pik
                pfcf[p] += addback
        # Adjust for unlevered tax: remove interest tax shield from PFCF
        # PFCF currently uses levered tax (with interest deductions).
        # Unlevered PFCF should use higher tax (no interest deductions).
        tax_out_irr = out.get("TAX_001") or out.get("TAX_DE_001") or out.get("TAX_LT_001") or out.get("TAX_AU_001")
        if tax_out_irr and hasattr(tax_out_irr, 'unlevered_tax_charge_accrued'):
            for p in range(n):
                tax_shield = tax_out_irr.unlevered_tax_charge_accrued[p] - tax_out_irr.tax_charge_accrued[p]
                pfcf[p] -= tax_shield  # higher tax -> lower PFCF

        # Equity cash flow from the equity HOLDER's perspective:
        # SHL is a separate instrument (earns its own PIK return).
        # ECF = -pure_equity + dividends + capital_reduction
        div_out = out.get("DIV_001")
        shl_out_ecf = out.get("SHL_001")
        if div_out and shl_out_ecf and config.shl and config.shl.equity_contributed:
            shl_pct = config.shl.shl_pct_of_equity
            ecf = [0.0] * n
            for p in range(n):
                eq_in = (1.0 - shl_pct) * config.shl.equity_contributed[p]
                div_p = div_out.dividends_paid[p]
                cap_red = div_out.capital_reduction[p]
                ecf[p] = -eq_in + div_p + cap_red
        else:
            # FCFE = CFO + CFI + net_borrowing = NCF + dividends + interest_paid
            # CF_001 puts interest in BOTH CFO (via NI) and CFF (as cash payment).
            # Adding back interest + dividends from NCF gives correct levered FCFE.
            _div_ecf = out.get("DIV_001")
            _pl_ecf = out.get("PL_001")
            if _div_ecf and _pl_ecf:
                ecf = [cf_out.net_cash_flow[p]
                       + _div_ecf.dividends_paid[p]
                       + _div_ecf.capital_reduction[p]
                       + _pl_ecf.interest_expense[p]
                       for p in range(n)]
            else:
                ecf = cf_out.net_cash_flow

        proj_rate = (
            sc.project_discount_rate if sc.project_discount_rate is not None
            else (wacc_out.wacc if wacc_out else 0.07)
        )
        eq_rate = (
            sc.equity_discount_rate if sc.equity_discount_rate is not None
            else (wacc_out.blended_cost_of_equity if wacc_out else 0.10)
        )

        irr_inputs = IRR_001.Inputs(
            periods=n,
            project_free_cash_flow=pfcf,
            equity_cash_flow=ecf,
            project_discount_rate=proj_rate,
            equity_discount_rate=eq_rate,
        )
        out["IRR_001"] = IRR_001.calculate(irr_inputs)

    # ------------------------------------------------------------------
    # Step 13.5: WORKING_CAPITAL_001 — receivables/payables
    # ------------------------------------------------------------------
    if config.working_capital is not None:
        out["WORKING_CAPITAL_001"] = WORKING_CAPITAL_001.calculate(
            _inject_timeline(config.working_capital, tl)
        )

    # ------------------------------------------------------------------
    # Step 13.6: SOURCES_USES_001 — construction S&U waterfall
    # ------------------------------------------------------------------
    if config.sources_uses is not None:
        out["SOURCES_USES_001"] = SOURCES_USES_001.calculate(
            _inject_timeline(config.sources_uses, tl)
        )

    # ------------------------------------------------------------------
    # Step 14.5: DASHBOARD_001 — KPI extraction
    # ------------------------------------------------------------------
    if config.dashboard is not None:
        out["DASHBOARD_001"] = DASHBOARD_001.calculate(config.dashboard)

    # ------------------------------------------------------------------
    # Step 14.7: VALUATION_001 — EV bridge / purchase price
    # ------------------------------------------------------------------
    if config.valuation is not None:
        out["VALUATION_001"] = VALUATION_001.calculate(config.valuation)

    # ------------------------------------------------------------------
    # Step 14.8: BREAKEVEN_001 — breakeven price analysis
    # ------------------------------------------------------------------
    if config.breakeven is not None:
        out["BREAKEVEN_001"] = BREAKEVEN_001.calculate(
            _inject_timeline(config.breakeven, tl)
        )

    # ------------------------------------------------------------------
    # Step 15: MODEL_CHECKS_001 — integrity and commercial checks
    # ------------------------------------------------------------------
    if config.model_checks is not None:
        out["MODEL_CHECKS_001"] = MODEL_CHECKS_001.calculate(
            _inject_timeline(config.model_checks, tl)
        )

    return result
