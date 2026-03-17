"""
assembly/perspectives.py

Perspective layer — sits ABOVE ProjectConfig.

Resolves high-level user intent (Seller, Bank, Screen, Import, Audit)
into a fully configured model run with the right modules enabled,
assumptions loaded, and formatting tier set.

This file does NOT execute the engine — it only decides what to run.
The actual time-series population happens downstream (from assumption_db,
parser, or user input).
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from assembly.engine import ProjectConfig, TimelineConfig, TaxConfig, StatementConfig


# ============================================================================
# ENUMS
# ============================================================================

class Perspective(str, Enum):
    SELLER = "seller"
    BANK = "bank"
    SCREEN = "screen"
    IMPORT = "import"
    AUDIT = "audit"


class DetailLevel(str, Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"
    AUTO = "auto"


class DebtStructure(str, Enum):
    SIMPLE = "simple"
    SCULPTED = "sculpted"
    LINEAR_MULTI = "linear"
    AUTO = "auto"


class FormattingTier(str, Enum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    PRESENTATION = "presentation"


# ============================================================================
# REQUEST / RESPONSE MODELS
# ============================================================================

class ModelRequest(BaseModel):
    """High-level user request — the intake form."""
    perspective: Perspective
    project_name: str = "New Project"
    market: str = "DK"
    technology: str = "PV"  # PV, BESS, WIND, HYBRID

    # Project parameters
    capacity_MW: float = Field(50.0, gt=0)
    bess_capacity_MW: Optional[float] = None
    bess_capacity_MWh: Optional[float] = None
    cod_year: int = Field(2027, ge=2024, le=2040)
    cod_month: int = Field(1, ge=1, le=12)
    lifetime_years: int = Field(35, ge=5, le=50)

    # Optional overrides
    detail_level: DetailLevel = DetailLevel.AUTO
    debt_structure: DebtStructure = DebtStructure.AUTO
    formatting_tier: Optional[FormattingTier] = None
    include_scenarios: Optional[bool] = None
    include_bess_repowering: Optional[bool] = None
    production_case: Optional[str] = None  # "P50" or "P90"

    # Import/Audit
    source_file: Optional[str] = None

    # PPA config
    ppa_share: Optional[float] = None


class ResolvedConfig(BaseModel):
    """Output of resolve() for the build path."""
    model_config = {"arbitrary_types_allowed": True}
    project_config: ProjectConfig
    formatting_tier: FormattingTier
    detail_level: DetailLevel
    headline_metrics: list[str]
    include_scenarios: bool
    scenario_presets: list[dict]
    perspective: Perspective
    notes: list[str]


class AuditConfig(BaseModel):
    """Output of resolve() for the audit path."""
    source_file: str
    market: str
    technology: str
    checks_to_run: list[str]
    rebuild_independently: bool = True
    compare_metrics: list[str]
    report_format: str = "xlsx"
    perspective: Perspective = Perspective.AUDIT
    notes: list[str]


# ============================================================================
# PERSPECTIVE DISPLAY METADATA
# ============================================================================

PERSPECTIVE_DISPLAY = {
    Perspective.SELLER: {
        "name": "Seller / transaction model",
        "description": "Convince an equity investor or IC to approve the deal",
        "audience": "Equity investor, Investment Committee, buyer",
    },
    Perspective.BANK: {
        "name": "Bank / lender model",
        "description": "Secure project finance debt — full monthly detail",
        "audience": "Project finance bank, credit committee",
    },
    Perspective.SCREEN: {
        "name": "EE internal screening",
        "description": "Quick assessment — should we pursue this project?",
        "audience": "Development team, Martin, Erik",
    },
    Perspective.IMPORT: {
        "name": "Import external model",
        "description": "Convert an advisor/seller model to EE standard",
        "audience": "EE deal team",
    },
    Perspective.AUDIT: {
        "name": "Model audit",
        "description": "Verify and challenge an external model — produce findings report",
        "audience": "EE deal team, transaction review",
    },
}


# ============================================================================
# PERSPECTIVE PRESETS
# ============================================================================

PERSPECTIVE_PRESETS: dict[Perspective, dict] = {
    Perspective.SELLER: {
        "detail_level": DetailLevel.AUTO,
        "formatting_tier": FormattingTier.PRESENTATION,
        "debt_structure": DebtStructure.SIMPLE,
        "production_case": "P50",
        "ppa_share": 0.50,
        "include_scenarios": True,
        "include_bess_repowering": True,
        "headline_metrics": [
            "equity_irr", "project_irr", "wacc_spread_bps",
            "ev_DKKk", "ev_per_MW", "profit_over_capex_pct",
        ],
        "modules_always": [
            "WACC_001", "CAPEX_001", "TAX", "PL_001", "CF_001", "BS_001",
            "IRR_001", "DASHBOARD_001", "VALUATION_001", "BREAKEVEN_001",
            "MODEL_CHECKS_001",
        ],
        "modules_never": [],
        "scenario_presets": [
            {"name": "Upside", "overrides": [
                {"path": "capex.sensitivity_factor", "value": -0.05},
            ]},
            {"name": "Downside", "overrides": [
                {"path": "capex.sensitivity_factor", "value": 0.10},
            ]},
        ],
        "notes": [
            "P50 production case — optimistic but defensible",
            "Simple debt structure — adjust to sculpted if bank-ready",
            "Valuation bridge and breakeven analysis included",
        ],
    },

    Perspective.BANK: {
        "detail_level": DetailLevel.MONTHLY,
        "formatting_tier": FormattingTier.PRESENTATION,
        "debt_structure": DebtStructure.SCULPTED,
        "production_case": "P90",
        "ppa_share": 0.70,
        "include_scenarios": True,
        "include_bess_repowering": True,
        "headline_metrics": [
            "min_dscr", "avg_dscr", "min_llcr", "avg_llcr",
            "gearing_pct", "total_debt_DKKk",
        ],
        "modules_always": [
            "WACC_001", "CAPEX_001", "DEBT_SCULPT_001", "DSRA_001",
            "CONSTR_FINANCE_001", "SHL_001", "VAT_FACILITY_001",
            "TAX", "PL_001", "CF_001", "BS_001", "IRR_001",
            "SOURCES_USES_001", "MODEL_CHECKS_001", "DASHBOARD_001",
        ],
        "modules_never": ["VALUATION_001", "BREAKEVEN_001"],
        "scenario_presets": [
            {"name": "DSCR stress", "overrides": [
                {"path": "capex.sensitivity_factor", "value": 0.15},
            ]},
            {"name": "Rate stress", "overrides": [
                {"path": "capex.sensitivity_factor", "value": 0.05},
            ]},
        ],
        "notes": [
            "P90 production case — conservative for credit",
            "Sculpted debt with DSRA and construction finance",
            "Full monthly detail — required by lenders",
            "Sources & Uses waterfall included",
        ],
    },

    Perspective.SCREEN: {
        "detail_level": DetailLevel.ANNUAL,
        "formatting_tier": FormattingTier.MINIMAL,
        "debt_structure": DebtStructure.SIMPLE,
        "production_case": "P50",
        "ppa_share": 0.40,
        "include_scenarios": True,
        "include_bess_repowering": False,
        "headline_metrics": [
            "project_irr", "wacc_spread_bps", "equity_irr",
            "profit_over_capex_pct", "min_dscr",
        ],
        "modules_always": [
            "WACC_001", "CAPEX_001", "TAX", "PL_001", "CF_001",
            "BS_001", "IRR_001", "BREAKEVEN_001", "DASHBOARD_001",
            "MODEL_CHECKS_001",
        ],
        "modules_never": [
            "DEBT_SCULPT_001", "DEBT_REFI_001", "DEBT_LINEAR_001",
            "CONSTR_FINANCE_001", "DSRA_001", "VAT_FACILITY_001",
            "SHL_001", "SOURCES_USES_001", "VALUATION_001",
            "WORKING_CAPITAL_001",
        ],
        "scenario_presets": [
            {"name": "+10% capex", "overrides": [
                {"path": "capex.sensitivity_factor", "value": 0.10},
            ]},
            {"name": "-10% revenue", "overrides": [
                {"path": "capex.sensitivity_factor", "value": -0.10},
            ]},
        ],
        "notes": [
            "Quick screening model — simplified debt, annual view",
            "Breakeven analysis shows minimum viable capture price",
            "Upgrade to Seller or Bank perspective for detailed output",
        ],
    },

    Perspective.IMPORT: {
        "detail_level": DetailLevel.AUTO,
        "formatting_tier": FormattingTier.STANDARD,
        "debt_structure": DebtStructure.AUTO,
        "production_case": None,
        "ppa_share": None,
        "include_scenarios": False,
        "include_bess_repowering": None,
        "headline_metrics": [
            "equity_irr", "project_irr", "min_dscr",
            "ev_DKKk", "total_capex_DKKk",
        ],
        "modules_always": [
            "WACC_001", "CAPEX_001", "TAX", "PL_001", "CF_001",
            "BS_001", "IRR_001", "MODEL_CHECKS_001", "DASHBOARD_001",
        ],
        "modules_never": [],
        "scenario_presets": [],
        "notes": [
            "Imported from external model — assumptions carried over",
            "EE standard formatting applied",
            "Run MODEL_CHECKS to verify integrity after import",
        ],
    },
}


# ============================================================================
# TECHNOLOGY → MODULE MAPPING
# ============================================================================

TECH_MODULES = {
    "PV":     {"rev": ["rev_pv"],   "opex": ["opex_pv"]},
    "BESS":   {"rev": ["rev_bess"], "opex": ["opex_bess"]},
    "WIND":   {"rev": ["rev_wind"], "opex": ["opex_wind"]},
    "HYBRID": {"rev": ["rev_pv", "rev_bess"], "opex": ["opex_pv", "opex_bess"]},
}

TAX_MAP = {
    "DK": "TAX_001",
    "DE": "TAX_DE_001",
}


# ============================================================================
# HELPERS
# ============================================================================

def get_tax_module(market: str) -> str:
    """Return which tax module to use for a market."""
    return TAX_MAP.get(market, "TAX_001")


def get_available_perspectives() -> list[dict]:
    """Return perspective metadata for UI display."""
    result = []
    for p in Perspective:
        display = PERSPECTIVE_DISPLAY[p]
        preset = PERSPECTIVE_PRESETS.get(p, {})
        result.append({
            "id": p.value,
            "name": display["name"],
            "description": display["description"],
            "audience": display["audience"],
            "headline_metrics": preset.get("headline_metrics", []),
            "is_build_path": p != Perspective.AUDIT,
        })
    return result


def _load_market_assumptions(market: str) -> dict:
    """Load assumption_db/{market}.json if it exists."""
    db_path = Path(__file__).parent.parent / "registry" / "assumption_db" / f"{market}.json"
    if db_path.exists():
        with open(db_path) as f:
            return json.load(f)
    return {}


# ============================================================================
# RESOLVE
# ============================================================================

def resolve(request: ModelRequest) -> ResolvedConfig | AuditConfig:
    """
    Resolve a ModelRequest into a fully configured ProjectConfig or AuditConfig.

    Build path (seller/bank/screen/import):
        1. Load perspective preset
        2. Apply user overrides
        3. Calculate timeline
        4. Determine which modules to enable based on technology + perspective
        5. Construct ProjectConfig with module slots set to None (disabled) or enabled
        6. Return ResolvedConfig

    Audit path:
        1. Validate source_file
        2. Return AuditConfig
    """
    # --- Audit path ---
    if request.perspective == Perspective.AUDIT:
        if not request.source_file:
            raise ValueError("Audit perspective requires source_file")
        return AuditConfig(
            source_file=request.source_file,
            market=request.market,
            technology=request.technology,
            checks_to_run=[
                "balance_sheet_alignment", "negative_cash", "income_statement_consistency",
                "depreciation_vs_capex", "dscr_covenant", "sources_uses_balanced",
                "negative_debt", "capex_within_budget",
            ],
            compare_metrics=[
                "equity_irr", "project_irr", "min_dscr", "total_capex",
                "net_revenue_lifetime", "total_opex_lifetime", "total_tax_lifetime",
            ],
            notes=[
                "Model will be parsed, rebuilt independently, and compared",
                "Findings report highlights any material deviations",
                f"Source file: {request.source_file}",
            ],
        )

    # --- Build path ---
    preset = PERSPECTIVE_PRESETS[request.perspective]

    # 1. Resolve effective values (user override beats preset)
    detail = request.detail_level if request.detail_level != DetailLevel.AUTO else preset["detail_level"]
    fmt_tier = request.formatting_tier or preset["formatting_tier"]
    debt_struct = request.debt_structure if request.debt_structure != DebtStructure.AUTO else preset["debt_structure"]
    include_scenarios = request.include_scenarios if request.include_scenarios is not None else preset["include_scenarios"]
    include_repow = request.include_bess_repowering if request.include_bess_repowering is not None else preset["include_bess_repowering"]
    ppa_share = request.ppa_share if request.ppa_share is not None else preset["ppa_share"]

    # 2. Calculate timeline
    construction_months = 12
    ops_months = request.lifetime_years * 12
    total_periods = construction_months + ops_months
    start_year = request.cod_year - 1
    start_month = request.cod_month

    timeline = TimelineConfig(
        periods=total_periods,
        start_year=start_year,
        start_month=start_month,
    )

    # 3. Build ProjectConfig with module slots
    tech = request.technology.upper()
    tech_mods = TECH_MODULES.get(tech, TECH_MODULES["PV"])
    modules_always = set(preset["modules_always"])
    modules_never = set(preset["modules_never"])

    # Start with everything disabled
    config_kwargs: dict[str, Any] = {
        "project_name": request.project_name,
        "market": request.market,
        "technology": request.technology,
        "timeline": timeline,
    }

    # WACC — always on if in modules_always
    if "WACC_001" in modules_always:
        import modules.core.WACC_001 as WACC_001
        assumptions = _load_market_assumptions(request.market)
        coc = assumptions.get("cost_of_capital", {})
        config_kwargs["wacc"] = WACC_001.Inputs(
            country=request.market,
            ppa_share=ppa_share or 0.5,
            debt_aud=100_000.0,
            equity_aud=50_000.0,
        )

    # Revenue modules per technology
    if "rev_pv" in tech_mods["rev"] and "REV_001" not in modules_never:
        import modules.revenue.REV_001 as REV_001
        config_kwargs["rev_pv"] = None  # caller populates
    if "rev_bess" in tech_mods["rev"] and "REV_002" not in modules_never:
        config_kwargs["rev_bess"] = None
    if "rev_wind" in tech_mods["rev"] and "REV_003" not in modules_never:
        config_kwargs["rev_wind"] = None

    # OPEX modules per technology
    if "opex_pv" in tech_mods["opex"] and "OPEX_001" not in modules_never:
        config_kwargs["opex_pv"] = None
    if "opex_bess" in tech_mods["opex"] and "OPEX_002" not in modules_never:
        config_kwargs["opex_bess"] = None
    if "opex_wind" in tech_mods["opex"] and "OPEX_003" not in modules_never:
        config_kwargs["opex_wind"] = None

    # CAPEX — always on
    config_kwargs["capex"] = None  # caller populates

    # Debt structure
    if debt_struct == DebtStructure.SCULPTED and "DEBT_SCULPT_001" not in modules_never:
        config_kwargs["debt_sculpt"] = None  # caller populates
    elif debt_struct == DebtStructure.LINEAR_MULTI and "DEBT_LINEAR_001" not in modules_never:
        config_kwargs["debt_linear"] = None
    elif "DEBT_001" not in modules_never:
        config_kwargs["debt"] = None  # caller populates

    # Additional debt modules
    if "DSRA_001" in modules_always and "DSRA_001" not in modules_never:
        config_kwargs["dsra"] = None
    if "CONSTR_FINANCE_001" in modules_always and "CONSTR_FINANCE_001" not in modules_never:
        config_kwargs["constr_finance"] = None
    if "SHL_001" in modules_always and "SHL_001" not in modules_never:
        config_kwargs["shl"] = None
    if "VAT_FACILITY_001" in modules_always and "VAT_FACILITY_001" not in modules_never:
        config_kwargs["vat_facility"] = None

    # Tax
    tax_module = get_tax_module(request.market)
    if tax_module == "TAX_DE_001" and "TAX_DE_001" not in modules_never:
        import modules.tax.TAX_DE_001 as TAX_DE_001
        config_kwargs["tax_de"] = None  # caller populates
    elif "TAX" in modules_always:
        config_kwargs["tax"] = None  # caller populates via TaxConfig

    # BESS repowering
    if include_repow and tech in ("BESS", "HYBRID"):
        config_kwargs["bess_repow"] = None
        config_kwargs["repow_debt"] = None

    # Statement modules — always via StatementConfig
    config_kwargs["statements"] = StatementConfig()

    # Sources & Uses
    if "SOURCES_USES_001" in modules_always and "SOURCES_USES_001" not in modules_never:
        config_kwargs["sources_uses"] = None

    # Dashboard
    if "DASHBOARD_001" in modules_always and "DASHBOARD_001" not in modules_never:
        config_kwargs["dashboard"] = None

    # Model checks
    if "MODEL_CHECKS_001" in modules_always and "MODEL_CHECKS_001" not in modules_never:
        config_kwargs["model_checks"] = None

    # Valuation
    if "VALUATION_001" in modules_always and "VALUATION_001" not in modules_never:
        config_kwargs["valuation"] = None

    # Breakeven
    if "BREAKEVEN_001" in modules_always and "BREAKEVEN_001" not in modules_never:
        config_kwargs["breakeven"] = None

    project_config = ProjectConfig(**config_kwargs)

    notes = list(preset["notes"])
    notes.append(f"Timeline: {total_periods} months ({construction_months}m construction + {ops_months}m operations)")
    notes.append(f"Technology: {tech} | Market: {request.market}")

    return ResolvedConfig(
        project_config=project_config,
        formatting_tier=fmt_tier,
        detail_level=detail,
        headline_metrics=list(preset["headline_metrics"]),
        include_scenarios=include_scenarios,
        scenario_presets=list(preset["scenario_presets"]),
        perspective=request.perspective,
        notes=notes,
    )
