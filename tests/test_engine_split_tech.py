"""Tests for split-tech engine capability."""

import pytest
from assembly.engine import (
    ProjectConfig, TimelineConfig, TaxConfig, StatementConfig,
    TechEntity, SPVStructure, run,
)
import modules.revenue.REV_001 as REV_001
import modules.revenue.REV_002 as REV_002
import modules.costs.OPEX_001 as OPEX_001
import modules.costs.OPEX_002 as OPEX_002
import modules.capex.CAPEX_001 as CAPEX_001
import modules.debt.DEBT_001 as DEBT_001
import modules.tax.TAX_AU_001 as TAX_AU_001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

N = 60  # 5 years
SY = 2025
SM = 1
COD_PV = 0    # PV already operational
COD_BESS = 12  # BESS COD after 12 months


def _pv_rev_inputs():
    """Simple PV revenue inputs."""
    return REV_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        net_production_MWh=[1000.0] * N,
        wholesale_price_DKK_per_MWh=[50.0] * N,
        capture_price_DKK_per_MWh=[45.0] * N,
        goo_price_DKK_per_MWh=[2.0] * N,
    )


def _bess_rev_inputs():
    """Simple BESS revenue (external mode)."""
    bess_rev = [0.0] * COD_BESS + [100.0] * (N - COD_BESS)
    return REV_002.Inputs(
        periods=N, start_year=SY, start_month=SM,
        power_MW=50.0, energy_MWh_installed=200.0,
        round_trip_efficiency=0.85,
        external_mode=True,
        external_bess_revenue_DKKk=bess_rev,
        discharge_volume_MWh=[0.0]*N,
        discharge_price_DKK_per_MWh=[0.0]*N,
        grid_charge_volume_MWh=[0.0]*N,
        grid_charge_price_DKK_per_MWh=[0.0]*N,
        pv_charge_volume_MWh=[0.0]*N,
        pv_charge_price_DKK_per_MWh=[0.0]*N,
        goo_price_DKK_per_MWh=[0.0]*N,
        multimarket_pct=[0.0]*N,
    )


def _pv_opex_inputs():
    return OPEX_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        capacity_mwp=100.0,
        om=OPEX_001.ComponentRate(annual_DKKk=500.0),
        commercial_management=OPEX_001.ComponentRate(annual_DKKk=0.0),
        inverter_replacement=OPEX_001.ComponentRate(annual_DKKk=0.0),
        insurance=OPEX_001.ComponentRate(annual_DKKk=100.0),
        other=OPEX_001.ComponentRate(annual_DKKk=0.0),
    )


def _bess_opex_inputs():
    return OPEX_002.Inputs(
        periods=N, start_year=SY, start_month=SM,
        om=OPEX_002.ComponentRate(annual_DKKk=300.0),
        insurance=OPEX_002.ComponentRate(annual_DKKk=50.0),
        other=OPEX_002.ComponentRate(annual_DKKk=0.0),
    )


def _split_config(**overrides):
    """Build a split-tech ProjectConfig with PV + BESS entities."""
    entities = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(),
            opex=_pv_opex_inputs(),
        ),
        TechEntity(
            tech_id="BESS", technology="BESS",
            cod_period=COD_BESS, asset_life_years=20,
            rev=_bess_rev_inputs(),
            opex=_bess_opex_inputs(),
        ),
    ]
    base = dict(
        project_name="Split-Tech Test",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        statements=StatementConfig(),
    )
    base.update(overrides)
    return ProjectConfig(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_backward_compat_no_tech_entities():
    """tech_entities=None runs single-pipeline mode exactly as before."""
    config = ProjectConfig(
        project_name="Single Pipeline",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        rev_pv=_pv_rev_inputs(),
        opex_pv=_pv_opex_inputs(),
        statements=StatementConfig(),
    )
    result = run(config)
    # Standard keys exist
    assert "PL_001" in result.outputs
    # No per-tech keys
    assert "PV_PL_001" not in result.outputs


def test_split_tech_per_tech_outputs_exist():
    """Split-tech mode creates per-tech PL/CF/BS outputs."""
    result = run(_split_config())
    assert result.get_tech("PV", "PL_001") is not None
    assert result.get_tech("BESS", "PL_001") is not None
    assert result.get_tech("PV", "CF_001") is not None
    assert result.get_tech("BESS", "CF_001") is not None
    assert result.get_tech("PV", "BS_001") is not None
    assert result.get_tech("BESS", "BS_001") is not None


def test_split_tech_combined_pl_exists():
    """Combined PL_001 is produced from consolidated per-tech data."""
    result = run(_split_config())
    assert "PL_001" in result.outputs


def test_consolidation_sums():
    """Combined revenue = PV revenue + BESS revenue."""
    result = run(_split_config())
    pv_pl = result.get_tech("PV", "PL_001")
    bess_pl = result.get_tech("BESS", "PL_001")
    combined_pl = result.outputs["PL_001"]
    for p in range(N):
        expected = pv_pl.gross_revenue[p] + bess_pl.gross_revenue[p]
        assert combined_pl.gross_revenue[p] == pytest.approx(expected, abs=0.1)


def test_per_tech_isolation():
    """Changing BESS revenue does not affect PV PL."""
    result1 = run(_split_config())
    pv_rev1 = sum(result1.get_tech("PV", "PL_001").gross_revenue)

    # Double BESS revenue
    entities2 = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
        ),
        TechEntity(
            tech_id="BESS", technology="BESS",
            cod_period=COD_BESS, asset_life_years=20,
            rev=REV_002.Inputs(
                periods=N, start_year=SY, start_month=SM,
                power_MW=50.0, energy_MWh_installed=200.0,
                round_trip_efficiency=0.85,
                external_mode=True,
                external_bess_revenue_DKKk=[0.0]*COD_BESS + [200.0]*(N-COD_BESS),
                discharge_volume_MWh=[0.0]*N,
                discharge_price_DKK_per_MWh=[0.0]*N,
                grid_charge_volume_MWh=[0.0]*N,
                grid_charge_price_DKK_per_MWh=[0.0]*N,
                pv_charge_volume_MWh=[0.0]*N,
                pv_charge_price_DKK_per_MWh=[0.0]*N,
                goo_price_DKK_per_MWh=[0.0]*N,
                multimarket_pct=[0.0]*N,
            ),
            opex=_bess_opex_inputs(),
        ),
    ]
    config2 = ProjectConfig(
        project_name="Isolation Test",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities2,
        statements=StatementConfig(),
    )
    result2 = run(config2)
    pv_rev2 = sum(result2.get_tech("PV", "PL_001").gross_revenue)
    assert pv_rev1 == pytest.approx(pv_rev2, rel=1e-6)


def test_different_cods():
    """PV revenue starts at period 0, BESS at period 12."""
    result = run(_split_config())
    pv_pl = result.get_tech("PV", "PL_001")
    bess_pl = result.get_tech("BESS", "PL_001")
    # PV has revenue from period 0
    assert pv_pl.gross_revenue[0] > 0
    # BESS has zero revenue before COD
    assert bess_pl.gross_revenue[0] == 0
    assert bess_pl.gross_revenue[5] == 0
    # BESS has revenue after COD
    assert bess_pl.gross_revenue[COD_BESS] > 0


def test_per_tech_tax():
    """independent_tax=True gives each tech its own tax calculation."""
    tax_cfg = TAX_AU_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        tax_rate=0.30,
        ebitda=[0.0]*N, interest_expense=[0.0]*N,
        capex_by_bucket=[[0.0]*N for _ in range(4)],
        depreciation_lifetimes=[30, 30, 30, 30],
    )
    entities = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
            tax_config=tax_cfg, independent_tax=True,
        ),
        TechEntity(
            tech_id="BESS", technology="BESS",
            cod_period=COD_BESS, asset_life_years=20,
            rev=_bess_rev_inputs(), opex=_bess_opex_inputs(),
            tax_config=tax_cfg, independent_tax=True,
        ),
    ]
    config = ProjectConfig(
        project_name="Tax Test",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        statements=StatementConfig(),
    )
    result = run(config)
    # Both techs should have tax outputs
    assert result.get_tech("PV", "TAX_AU_001") is not None
    assert result.get_tech("BESS", "TAX_AU_001") is not None


def test_dual_spv_raises():
    """dual_spv mode raises NotImplementedError."""
    with pytest.raises(NotImplementedError, match="dual_spv"):
        _split_config(spv_structure=SPVStructure.DUAL)


def test_conflict_validation():
    """tech_entities + rev_pv raises ValueError."""
    with pytest.raises(ValueError, match="Cannot set both"):
        ProjectConfig(
            project_name="Conflict",
            timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
            tech_entities=[TechEntity(tech_id="PV", technology="PV")],
            rev_pv=_pv_rev_inputs(),
        )


def test_get_tech_helper():
    """result.get_tech('PV', 'PL_001') returns correct output."""
    result = run(_split_config())
    pv_pl = result.get_tech("PV", "PL_001")
    assert pv_pl is result.outputs["PV_PL_001"]


def test_single_tech_entity():
    """One TechEntity only — combined == per-tech."""
    config = ProjectConfig(
        project_name="Single Tech Entity",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=[
            TechEntity(
                tech_id="PV", technology="PV",
                cod_period=COD_PV, asset_life_years=30,
                rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
            ),
        ],
        statements=StatementConfig(),
    )
    result = run(config)
    pv_pl = result.get_tech("PV", "PL_001")
    combined = result.outputs["PL_001"]
    for p in range(N):
        assert combined.gross_revenue[p] == pytest.approx(pv_pl.gross_revenue[p], abs=0.1)


def test_three_tech_entities():
    """PV + BESS + Wind entities all produce per-tech outputs."""
    entities = [
        TechEntity(tech_id="PV", technology="PV",
                   rev=_pv_rev_inputs(), opex=_pv_opex_inputs()),
        TechEntity(tech_id="BESS", technology="BESS",
                   rev=_bess_rev_inputs(), opex=_bess_opex_inputs()),
        TechEntity(tech_id="WIND", technology="WIND"),  # no rev/opex — empty
    ]
    config = ProjectConfig(
        project_name="Three Tech",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        statements=StatementConfig(),
    )
    result = run(config)
    assert result.get_tech("PV", "PL_001") is not None
    assert result.get_tech("BESS", "PL_001") is not None
    assert result.get_tech("WIND", "PL_001") is not None


def test_per_tech_cf_unlevered():
    """Per-tech CF has zero debt (Phase 1 — unlevered)."""
    result = run(_split_config())
    pv_cf = result.get_tech("PV", "CF_001")
    # CFF should be all zeros (no debt)
    assert all(v == pytest.approx(0.0) for v in pv_cf.cff)


def test_per_tech_bs_no_debt():
    """Per-tech BS has zero debt balance when no debt configured."""
    result = run(_split_config())
    pv_bs = result.get_tech("PV", "BS_001")
    # Debt balance should be zero
    for p in range(N):
        assert pv_bs.debt_balance[p] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Phase 1: CAPEX consolidation tests
# ---------------------------------------------------------------------------

def _pv_capex_inputs():
    return CAPEX_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        epc_DKKk=50_000.0,
        grid_connection_DKKk=5_000.0,
        development_DKKk=2_000.0,
        contingency_pct=0.05,
        construction_start_period=0,
        construction_periods=6,
        drawdown_profile=[1/6]*6,
    )


def _bess_capex_inputs():
    return CAPEX_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        epc_DKKk=30_000.0,
        grid_connection_DKKk=3_000.0,
        development_DKKk=1_000.0,
        contingency_pct=0.05,
        construction_start_period=6,
        construction_periods=6,
        drawdown_profile=[1/6]*6,
    )


def _split_config_with_capex(**overrides):
    """Split-tech config with per-tech CAPEX."""
    entities = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
            capex=_pv_capex_inputs(),
        ),
        TechEntity(
            tech_id="BESS", technology="BESS",
            cod_period=COD_BESS, asset_life_years=20,
            rev=_bess_rev_inputs(), opex=_bess_opex_inputs(),
            capex=_bess_capex_inputs(),
        ),
    ]
    base = dict(
        project_name="Split-Tech CAPEX Test",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        statements=StatementConfig(),
    )
    base.update(overrides)
    return ProjectConfig(**base)


def test_capex_consolidation():
    """_split_tech_capex equals sum of per-tech CAPEX."""
    result = run(_split_config_with_capex())
    pv_capex = result.get_tech("PV", "CAPEX_001")
    bess_capex = result.get_tech("BESS", "CAPEX_001")
    combined_capex = result.outputs["_split_tech_capex"]
    for p in range(N):
        expected = pv_capex.total_capex_monthly[p] + bess_capex.total_capex_monthly[p]
        assert combined_capex[p] == pytest.approx(expected, abs=0.01)


def test_capex_cumulative_consolidation():
    """_split_tech_cumulative_capex equals sum of per-tech cumulative."""
    result = run(_split_config_with_capex())
    pv_capex = result.get_tech("PV", "CAPEX_001")
    bess_capex = result.get_tech("BESS", "CAPEX_001")
    combined = result.outputs["_split_tech_cumulative_capex"]
    for p in range(N):
        expected = pv_capex.cumulative_capex[p] + bess_capex.cumulative_capex[p]
        assert combined[p] == pytest.approx(expected, abs=0.01)


def test_combined_cf_uses_split_tech_capex():
    """Combined CF_001 capex_monthly matches consolidated CAPEX."""
    result = run(_split_config_with_capex())
    combined_cf = result.outputs["CF_001"]
    combined_capex = result.outputs["_split_tech_capex"]
    for p in range(N):
        assert combined_cf.capex_monthly[p] == pytest.approx(combined_capex[p], abs=0.01)


# ---------------------------------------------------------------------------
# Phase 2: Debt allocation tests
# ---------------------------------------------------------------------------

def _split_config_with_debt():
    """Split-tech config with per-tech CAPEX + combined debt."""
    pv_capex = _pv_capex_inputs()
    bess_capex = _bess_capex_inputs()
    # Drawdown schedule — PV draws in months 0-5, BESS in months 6-11
    drawdowns = [0.0] * N
    pv_total = 50_000.0 + 5_000.0 + 2_000.0  # ~59,850 with contingency
    bess_total = 30_000.0 + 3_000.0 + 1_000.0
    for p in range(6):
        drawdowns[p] = pv_total / 6
    for p in range(6, 12):
        drawdowns[p] = bess_total / 6
    facility = sum(drawdowns)

    debt = DEBT_001.Inputs(
        periods=N, start_year=SY, start_month=SM,
        facility=facility,
        all_in_rate=0.05,
        repayment_type="annuity",
        tenor_months=36,
        drawdowns=drawdowns,
        repayment_start_period=12,
    )

    entities = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
            capex=pv_capex,
        ),
        TechEntity(
            tech_id="BESS", technology="BESS",
            cod_period=COD_BESS, asset_life_years=20,
            rev=_bess_rev_inputs(), opex=_bess_opex_inputs(),
            capex=bess_capex,
        ),
    ]
    return ProjectConfig(
        project_name="Split-Tech Debt Test",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        debt=debt,
        statements=StatementConfig(),
    )


def test_debt_allocation_exists():
    """Debt allocation creates per-tech DEBT_ALLOC entries."""
    result = run(_split_config_with_debt())
    assert "PV_DEBT_ALLOC" in result.outputs
    assert "BESS_DEBT_ALLOC" in result.outputs


def test_debt_allocation_sums_to_combined():
    """Allocated interest across techs sums to combined interest."""
    result = run(_split_config_with_debt())
    pv_alloc = result.outputs["PV_DEBT_ALLOC"]
    bess_alloc = result.outputs["BESS_DEBT_ALLOC"]
    debt_out = result.outputs["DEBT_001"]
    for p in range(N):
        allocated_sum = pv_alloc["interest"][p] + bess_alloc["interest"][p]
        assert allocated_sum == pytest.approx(debt_out.interest[p], abs=0.01)


def test_debt_allocation_principal_sums():
    """Allocated principal across techs sums to combined principal."""
    result = run(_split_config_with_debt())
    pv_alloc = result.outputs["PV_DEBT_ALLOC"]
    bess_alloc = result.outputs["BESS_DEBT_ALLOC"]
    debt_out = result.outputs["DEBT_001"]
    for p in range(N):
        allocated = pv_alloc["principal"][p] + bess_alloc["principal"][p]
        expected = debt_out.principal[p] if debt_out.principal and p < len(debt_out.principal) else 0.0
        assert allocated == pytest.approx(expected, abs=0.01)


def test_per_tech_levered_pl_has_interest():
    """After debt allocation, per-tech PL has non-zero interest."""
    result = run(_split_config_with_debt())
    pv_pl = result.get_tech("PV", "PL_001")
    # Interest should be non-zero during repayment period
    total_interest = sum(pv_pl.interest_expense)
    assert total_interest > 0.1, "PV PL should have allocated interest"


def test_per_tech_levered_cf_has_debt():
    """After debt allocation, per-tech CF has non-zero debt flows."""
    result = run(_split_config_with_debt())
    pv_cf = result.get_tech("PV", "CF_001")
    total_drawdown = sum(pv_cf.debt_drawdown) if pv_cf.debt_drawdown else 0
    total_principal = sum(pv_cf.principal_repayment) if pv_cf.principal_repayment else 0
    assert total_drawdown > 0.1, "PV CF should have allocated drawdown"
    assert total_principal > 0.1, "PV CF should have allocated principal"


def test_per_tech_levered_bs_has_debt():
    """After debt allocation, per-tech BS has non-zero debt balance."""
    result = run(_split_config_with_debt())
    pv_bs = result.get_tech("PV", "BS_001")
    # During drawdown/repayment period, debt should be positive
    max_debt = max(pv_bs.debt_balance)
    assert max_debt > 0.1, "PV BS should have allocated debt balance"


def test_single_tech_gets_full_allocation():
    """Single tech entity gets 100% of debt."""
    entities = [
        TechEntity(
            tech_id="PV", technology="PV",
            cod_period=COD_PV, asset_life_years=30,
            rev=_pv_rev_inputs(), opex=_pv_opex_inputs(),
            capex=_pv_capex_inputs(),
        ),
    ]
    drawdowns = [0.0] * N
    for p in range(6):
        drawdowns[p] = 10_000.0
    config = ProjectConfig(
        project_name="Single Tech Debt",
        timeline=TimelineConfig(periods=N, start_year=SY, start_month=SM),
        tech_entities=entities,
        debt=DEBT_001.Inputs(
            periods=N, start_year=SY, start_month=SM,
            facility=60_000.0, all_in_rate=0.05,
            repayment_type="annuity", tenor_months=36,
            drawdowns=drawdowns, repayment_start_period=6,
        ),
        statements=StatementConfig(),
    )
    result = run(config)
    pv_alloc = result.outputs["PV_DEBT_ALLOC"]
    debt_out = result.outputs["DEBT_001"]
    for p in range(N):
        assert pv_alloc["interest"][p] == pytest.approx(debt_out.interest[p], abs=0.01)


def test_no_debt_no_allocation():
    """Without debt, no DEBT_ALLOC keys exist."""
    result = run(_split_config_with_capex())
    assert "PV_DEBT_ALLOC" not in result.outputs
    assert "BESS_DEBT_ALLOC" not in result.outputs


# ---------------------------------------------------------------------------
# Phase 3: Per-tech Excel sheet tests
# ---------------------------------------------------------------------------

def test_build_per_tech_mappings():
    """build_per_tech_mappings generates correct ROW_MAP entries."""
    from assembly.cell_mapper import build_per_tech_mappings
    rm, ms = build_per_tech_mappings(["PV", "BESS"])
    # Should have PV.FinStat entries for PL/CF/BS
    assert ("PV.FinStat", "PV_PL_001", "gross_revenue") in rm
    assert ("PV.FinStat", "PV_CF_001", "closing_cash") in rm
    assert ("PV.FinStat", "PV_BS_001", "debt_balance") in rm
    assert ("BESS.FinStat", "BESS_PL_001", "net_income") in rm
    # MODULE_SHEET mappings
    assert ms["PV_PL_001"] == "PV.FinStat"
    assert ms["BESS_CF_001"] == "BESS.FinStat"


def test_per_tech_mappings_mirror_statements_rows():
    """Per-tech ROW_MAP uses same row numbers as Statements."""
    from assembly.cell_mapper import build_per_tech_mappings, ROW_MAP
    rm, _ = build_per_tech_mappings(["PV"])
    # PL gross_revenue should be at same row as Statements PL
    statements_row = ROW_MAP[("Statements", "PL_001", "gross_revenue")]
    per_tech_row = rm[("PV.FinStat", "PV_PL_001", "gross_revenue")]
    assert per_tech_row == statements_row
