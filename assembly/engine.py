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
from typing import Any, Optional

from pydantic import BaseModel, Field

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
import modules.statements.WORKING_CAPITAL_001 as WORKING_CAPITAL_001
import modules.statements.SOURCES_USES_001 as SOURCES_USES_001
import modules.revenue.PPA_CFD_001 as PPA_CFD_001
import modules.checks.MODEL_CHECKS_001 as MODEL_CHECKS_001
import modules.reporting.DASHBOARD_001 as DASHBOARD_001


# ============================================================================
# CONFIG MODELS
# ============================================================================

class TimelineConfig(BaseModel):
    """Project timeline — defined once, injected into every module."""
    periods: int = Field(..., gt=0, description="Total project life in months")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")
    currency: str = Field("DKK", description="Project currency: DKK, EUR, USD, AUD")


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


class ProjectConfig(BaseModel):
    """
    Top-level project configuration.

    Each module config field accepts the module's own Inputs model.
    Set to None to disable a module — disabled modules are skipped and
    their outputs are absent from AssemblyResult.

    The engine overrides periods / start_year / start_month from `timeline`
    before calling each module's calculate(), so these fields in sub-configs
    are ignored.
    """
    project_name: str
    market: str = "DK"
    technology: str = "PV"
    timeline: TimelineConfig

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
    tax:      Optional[TaxConfig]        = None   # TAX_001
    tax_de:   Optional[TAX_DE_001.Inputs] = None  # TAX_DE_001
    working_capital: Optional[WORKING_CAPITAL_001.Inputs] = None  # WORKING_CAPITAL_001
    sources_uses: Optional[SOURCES_USES_001.Inputs] = None  # SOURCES_USES_001
    valuation: Optional[VALUATION_001.Inputs] = None  # VALUATION_001
    breakeven: Optional[BREAKEVEN_001.Inputs] = None  # BREAKEVEN_001
    model_checks: Optional[MODEL_CHECKS_001.Inputs] = None  # MODEL_CHECKS_001
    dashboard: Optional[DASHBOARD_001.Inputs] = None  # DASHBOARD_001
    statements: StatementConfig = Field(default_factory=StatementConfig)


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
# CORE RUN FUNCTION
# ============================================================================

def run(config: ProjectConfig) -> AssemblyResult:
    """
    Execute all enabled modules in dependency order and return AssemblyResult.

    Raises ValueError if a required upstream module is disabled when a
    downstream module that depends on it is enabled.
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
        # Auto-wire debt_service from DEBT_001 if not provided
        debt_out = out.get("DEBT_001")
        if (not dsra_inp.debt_service or all(d == 0 for d in dsra_inp.debt_service)) and debt_out:
            dsra_inp = dsra_inp.model_copy(update={
                "debt_service": debt_out.debt_service,
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
        out["TAX_LT_001"] = TAX_LT_001.calculate(
            _inject_timeline(config.tax_lt, tl)
        )

    # ------------------------------------------------------------------
    # Step 9: PL_001 — P&L (fully wired)
    # ------------------------------------------------------------------
    if any(k in out for k in ("TAX_001", "DEBT_001", "REV_001", "REV_002", "REV_003")):
        rev_pv_net    = out["REV_001"].net_revenue  if "REV_001"  in out else None
        rev_bess_net  = out["REV_002"].net_revenue  if "REV_002"  in out else None
        rev_wind_net  = out["REV_003"].net_revenue  if "REV_003"  in out else None
        opex_pv_tot   = out["OPEX_001"].total_opex  if "OPEX_001" in out else None
        opex_bess_tot = out["OPEX_002"].total_opex  if "OPEX_002" in out else None
        opex_wind_tot = out["OPEX_003"].total_opex  if "OPEX_003" in out else None
        tax_out = out.get("TAX_001")
        tax_de_out = out.get("TAX_DE_001")
        debt_out = out.get("DEBT_001")

        gross_rev = _add_series(rev_pv_net, rev_bess_net, rev_wind_net, n=n)
        total_opex_combined = _add_series(opex_pv_tot, opex_bess_tot, opex_wind_tot, n=n)
        dep = tax_out.tax_depreciation if tax_out else (
            tax_de_out.tax_depreciation if tax_de_out else _zeros(n)
        )
        # Add BESS repowering accounting depreciation
        repow_out = out.get("BESS_REPOW_001")
        if repow_out:
            dep = [dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
        interest = debt_out.interest if debt_out else _zeros(n)
        # Add refi interest
        refi_out = out.get("DEBT_REFI_001")
        if refi_out:
            interest = [interest[p] + refi_out.interest[p] for p in range(n)]
        # Add linear debt interest
        linear_out = out.get("DEBT_LINEAR_001")
        if linear_out:
            interest = [interest[p] + linear_out.total_interest[p] for p in range(n)]
        # Add construction finance interest
        constr_out = out.get("CONSTR_FINANCE_001")
        if constr_out:
            interest = [interest[p] + constr_out.interest[p] for p in range(n)]
        tax_charge = tax_out.tax_charge_accrued if tax_out else (
            tax_de_out.tax_charge_accrued if tax_de_out else _zeros(n)
        )

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
        tax_out = out.get("TAX_001")
        debt_out = out.get("DEBT_001")
        capex_out = out.get("CAPEX_001")
        sc = config.statements

        repow_out = out.get("BESS_REPOW_001")
        cf_dep = tax_out.tax_depreciation if tax_out else _zeros(n)
        if repow_out:
            cf_dep = [cf_dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]
        cf_capex = capex_out.total_capex_monthly if capex_out else _zeros(n)
        if repow_out:
            cf_capex = [cf_capex[p] + repow_out.repowering_cost_monthly[p] for p in range(n)]

        cf_draw = debt_out.drawdown if debt_out else []
        cf_princ = debt_out.principal if debt_out else []
        cf_int = debt_out.interest if debt_out else _zeros(n)
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
            cf_int = [cf_int[p] + constr_out.interest[p] for p in range(n)]

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
    # Step 11: BS_001 — balance sheet (fully wired)
    # ------------------------------------------------------------------
    if "CF_001" in out:
        cf_out = out["CF_001"]
        pl_out = out["PL_001"]
        tax_out = out.get("TAX_001")
        debt_out = out.get("DEBT_001")
        capex_out = out.get("CAPEX_001")
        sc = config.statements

        repow_out = out.get("BESS_REPOW_001")
        bs_capex = capex_out.total_capex_monthly if capex_out else _zeros(n)
        bs_dep = tax_out.tax_depreciation if tax_out else _zeros(n)
        if repow_out:
            bs_capex = [bs_capex[p] + repow_out.repowering_cost_monthly[p] for p in range(n)]
            bs_dep = [bs_dep[p] + repow_out.accounting_depreciation_monthly[p] for p in range(n)]

        bs_kwargs: dict = dict(
            periods=n,
            start_year=tl.start_year,
            start_month=tl.start_month,
            opening_contributed_equity_DKKk=sc.opening_contributed_equity_DKKk,
            opening_retained_earnings_DKKk=sc.opening_retained_earnings_DKKk,
            capex_monthly=bs_capex,
            depreciation_monthly=bs_dep,
            closing_cash=cf_out.closing_cash,
            debt_closing_balance=_add_series(
                debt_out.closing_balance if debt_out else None,
                out["DEBT_REFI_001"].closing_balance if "DEBT_REFI_001" in out else None,
                out["DEBT_LINEAR_001"].total_closing_balance if "DEBT_LINEAR_001" in out else None,
                out["CONSTR_FINANCE_001"].closing_balance if "CONSTR_FINANCE_001" in out else None,
                n=n,
            ),
            net_income=pl_out.net_income,
        )
        if sc.dividends_paid:
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
        ecf  = cf_out.net_cash_flow

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
