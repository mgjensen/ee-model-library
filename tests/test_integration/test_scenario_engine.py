"""Tests for assembly/scenario_engine.py — multi-scenario runner."""

import math
import pytest
from assembly.scenario_engine import (
    SensiParameter, ScenarioConfig, ScenarioKPIs, ScenarioResult,
    run_scenarios, _apply_parameter, _extract_kpis, _get_nested, _set_nested,
)
from assembly.engine import ProjectConfig, TimelineConfig, run


# ---------------------------------------------------------------------------
# Helpers — minimal config that runs through engine
# ---------------------------------------------------------------------------

def _base_config():
    """Minimal ProjectConfig with WACC only (fastest to run)."""
    import modules.core.WACC_001 as WACC_001
    return ProjectConfig(
        project_name="Scenario Test",
        market="DK",
        technology="PV",
        timeline=TimelineConfig(periods=24, start_year=2026, start_month=1),
        wacc=WACC_001.Inputs(
            country="DK",
            ppa_share=0.6,
            debt_aud=150_000.0,
            equity_aud=50_000.0,
        ),
    )


# ---------------------------------------------------------------------------
# _get_nested / _set_nested
# ---------------------------------------------------------------------------

def test_get_nested_simple():
    cfg = _base_config()
    assert _get_nested(cfg, "project_name") == "Scenario Test"


def test_get_nested_deep():
    cfg = _base_config()
    assert _get_nested(cfg, "timeline.periods") == 24


def test_set_nested_simple():
    cfg = _base_config()
    updated = _set_nested(cfg, "project_name", "New Name")
    assert updated.project_name == "New Name"


def test_set_nested_deep():
    cfg = _base_config()
    updated = _set_nested(cfg, "timeline.periods", 36)
    assert updated.timeline.periods == 36


# ---------------------------------------------------------------------------
# _apply_parameter
# ---------------------------------------------------------------------------

def test_apply_pct_delta():
    cfg = _base_config()
    param = SensiParameter(
        name="test", param_type="pct_delta",
        target_field="timeline.periods", values=[0.5],
    )
    updated = _apply_parameter(cfg, param, 0.5)
    assert updated.timeline.periods == 36  # 24 * 1.5


def test_apply_absolute():
    cfg = _base_config()
    param = SensiParameter(
        name="test", param_type="absolute",
        target_field="project_name", values=["Override"],
    )
    updated = _apply_parameter(cfg, param, "Override")
    assert updated.project_name == "Override"


def test_apply_switch():
    cfg = _base_config()
    param = SensiParameter(
        name="test", param_type="switch",
        target_field="wacc", values=[0],
    )
    # Switch to False — but wacc is a model, this tests the bool conversion
    # Just verify no crash
    updated = _apply_parameter(cfg, param, 0)
    assert updated is not None


# ---------------------------------------------------------------------------
# run_scenarios
# ---------------------------------------------------------------------------

def test_base_case_runs():
    sc = ScenarioConfig(base_config=_base_config())
    result = run_scenarios(sc)
    assert result.base_case.name == "Base Case"


def test_empty_scenarios_returns_base_only():
    sc = ScenarioConfig(base_config=_base_config(), scenario_names=[])
    result = run_scenarios(sc)
    assert len(result.scenarios) == 0
    assert result.base_case is not None


def test_scenario_names_match():
    sc = ScenarioConfig(
        base_config=_base_config(),
        scenario_names=["Low", "High"],
        parameters=[
            SensiParameter(
                name="test", param_type="absolute",
                target_field="project_name", values=["Low Case", "High Case"],
            ),
        ],
    )
    result = run_scenarios(sc)
    assert len(result.scenarios) == 2
    assert result.scenarios[0].name == "Low"
    assert result.scenarios[1].name == "High"


def test_scenarios_independent():
    """Each scenario starts from base, not from previous."""
    sc = ScenarioConfig(
        base_config=_base_config(),
        scenario_names=["A", "B"],
        parameters=[],
    )
    result = run_scenarios(sc)
    # Both should have same KPIs as base (no overrides)
    # NaN == NaN is False, so compare ev_DKKk instead
    assert result.scenarios[0].ev_DKKk == result.scenarios[1].ev_DKKk


# ---------------------------------------------------------------------------
# KPI extraction
# ---------------------------------------------------------------------------

def test_kpi_extraction_from_base():
    result = run(_base_config())
    kpis = _extract_kpis("Test", result)
    assert kpis.name == "Test"
    assert isinstance(kpis.project_irr, float)


def test_kpi_no_debt_zeros():
    """Config without debt → zero debt KPIs."""
    result = run(_base_config())
    kpis = _extract_kpis("Test", result)
    assert kpis.senior_debt_DKKk == 0.0
    assert kpis.gearing_pct == 0.0


# ---------------------------------------------------------------------------
# Tornado data
# ---------------------------------------------------------------------------

def test_tornado_data_structure():
    sc = ScenarioConfig(
        base_config=_base_config(),
        scenario_names=["Low", "High"],
        parameters=[
            SensiParameter(
                name="capex", param_type="absolute",
                target_field="project_name", values=["L", "H"],
            ),
        ],
    )
    result = run_scenarios(sc)
    assert len(result.tornado_data) == 1
    assert result.tornado_data[0]["parameter"] == "capex"


def test_scenario_result_is_model():
    sc = ScenarioConfig(base_config=_base_config())
    result = run_scenarios(sc)
    assert isinstance(result, ScenarioResult)
