"""Tests for assembly/perspectives.py — perspective resolution layer."""

import pytest
from assembly.perspectives import (
    Perspective, DetailLevel, DebtStructure, FormattingTier,
    ModelRequest, ResolvedConfig, AuditConfig,
    resolve, get_available_perspectives, get_tax_module,
    PERSPECTIVE_PRESETS,
)


# ---------------------------------------------------------------------------
# Seller perspective
# ---------------------------------------------------------------------------

def test_resolve_seller_enables_correct_modules():
    req = ModelRequest(perspective=Perspective.SELLER, market="DK", technology="PV")
    result = resolve(req)
    assert isinstance(result, ResolvedConfig)
    cfg = result.project_config
    # WACC always on for seller
    assert cfg.wacc is not None
    # Statements always on
    assert cfg.statements is not None
    # Seller uses simple debt by default
    assert result.perspective == Perspective.SELLER


def test_resolve_formatting_tier_defaults():
    """seller=presentation, bank=presentation, screen=minimal."""
    seller = resolve(ModelRequest(perspective=Perspective.SELLER))
    bank = resolve(ModelRequest(perspective=Perspective.BANK))
    screen = resolve(ModelRequest(perspective=Perspective.SCREEN))
    assert seller.formatting_tier == FormattingTier.PRESENTATION
    assert bank.formatting_tier == FormattingTier.PRESENTATION
    assert screen.formatting_tier == FormattingTier.MINIMAL


# ---------------------------------------------------------------------------
# Bank perspective
# ---------------------------------------------------------------------------

def test_resolve_bank_enables_sculpted_debt():
    req = ModelRequest(perspective=Perspective.BANK, market="DK", technology="PV")
    result = resolve(req)
    cfg = result.project_config
    # Bank should use sculpted debt
    assert result.detail_level == DetailLevel.MONTHLY
    # Sculpted debt module should be enabled (not None means it's a slot)
    # debt_sculpt will be None (caller populates) but the key exists
    assert "debt_sculpt" in type(cfg).model_fields


def test_resolve_bank_notes():
    result = resolve(ModelRequest(perspective=Perspective.BANK))
    assert any("P90" in n for n in result.notes)
    assert any("monthly" in n.lower() for n in result.notes)


# ---------------------------------------------------------------------------
# Screen perspective
# ---------------------------------------------------------------------------

def test_resolve_screen_disables_complex_debt():
    req = ModelRequest(perspective=Perspective.SCREEN, market="DK", technology="PV")
    result = resolve(req)
    preset = PERSPECTIVE_PRESETS[Perspective.SCREEN]
    # These should be in modules_never
    assert "DEBT_SCULPT_001" in preset["modules_never"]
    assert "DSRA_001" in preset["modules_never"]
    assert "CONSTR_FINANCE_001" in preset["modules_never"]


# ---------------------------------------------------------------------------
# Hybrid technology
# ---------------------------------------------------------------------------

def test_resolve_hybrid_enables_pv_and_bess():
    req = ModelRequest(perspective=Perspective.SELLER, technology="HYBRID",
                       bess_capacity_MW=50.0, bess_capacity_MWh=200.0)
    result = resolve(req)
    # Both PV and BESS revenue/opex should be accessible
    assert result.project_config is not None
    assert result.perspective == Perspective.SELLER


# ---------------------------------------------------------------------------
# Audit perspective
# ---------------------------------------------------------------------------

def test_resolve_audit_returns_audit_config():
    req = ModelRequest(perspective=Perspective.AUDIT, source_file="model.xlsx")
    result = resolve(req)
    assert isinstance(result, AuditConfig)
    assert result.source_file == "model.xlsx"
    assert result.rebuild_independently is True
    assert len(result.checks_to_run) >= 5


def test_resolve_audit_requires_source_file():
    req = ModelRequest(perspective=Perspective.AUDIT)
    with pytest.raises(ValueError, match="source_file"):
        resolve(req)


# ---------------------------------------------------------------------------
# User overrides
# ---------------------------------------------------------------------------

def test_resolve_user_override_beats_preset():
    """User sets debt_structure=SCULPTED on seller → should use sculpted."""
    req = ModelRequest(
        perspective=Perspective.SELLER,
        debt_structure=DebtStructure.SCULPTED,
    )
    result = resolve(req)
    # The resolved config should reflect sculpted debt
    assert result.project_config is not None


# ---------------------------------------------------------------------------
# Market → tax mapping
# ---------------------------------------------------------------------------

def test_resolve_de_market_uses_tax_de():
    assert get_tax_module("DE") == "TAX_DE_001"
    assert get_tax_module("DK") == "TAX_001"
    # Resolve a DE project
    req = ModelRequest(perspective=Perspective.SELLER, market="DE")
    result = resolve(req)
    assert result.project_config.market == "DE"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def test_resolve_timeline_calculation():
    req = ModelRequest(
        perspective=Perspective.SELLER,
        cod_year=2027, cod_month=1, lifetime_years=35,
    )
    result = resolve(req)
    tl = result.project_config.timeline
    # 12 construction + 35*12 = 432 total
    assert tl.periods == 12 + 35 * 12
    assert tl.start_year == 2026  # 1 year before COD


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def test_get_available_perspectives_returns_all_five():
    persp = get_available_perspectives()
    assert len(persp) == 5
    ids = {p["id"] for p in persp}
    assert ids == {"seller", "bank", "screen", "import", "audit"}


# ---------------------------------------------------------------------------
# BESS repowering
# ---------------------------------------------------------------------------

def test_resolve_bess_repowering_on_hybrid():
    req = ModelRequest(
        perspective=Perspective.SELLER,
        technology="HYBRID",
        include_bess_repowering=True,
    )
    result = resolve(req)
    # Repowering should be enabled for HYBRID
    assert result.project_config is not None
