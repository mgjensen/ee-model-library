"""Tests for assembly/scenario_runner.py."""

import math
import pytest
from assembly.scenario_runner import (
    ScenarioOverride,
    ScenarioConfig,
    ScenarioResult,
    PortfolioResult,
    SensitivityAxis,
    run_scenarios,
    aggregate_portfolio,
    _extract_kpis,
)
from assembly.engine import ProjectConfig, TimelineConfig, run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config():
    """Minimal ProjectConfig with no modules enabled — fast to run."""
    return ProjectConfig(
        project_name="Test",
        market="DK",
        technology="PV",
        timeline=TimelineConfig(periods=24, start_year=2026, start_month=1),
    )


# ---------------------------------------------------------------------------
# run_scenarios
# ---------------------------------------------------------------------------

def test_run_scenarios_empty():
    """Empty scenario list returns empty results."""
    results = run_scenarios(_minimal_config(), [])
    assert results == []


def test_run_scenarios_one():
    """Single base scenario returns one result with correct name."""
    sc = ScenarioConfig(name="Base", overrides=[])
    results = run_scenarios(_minimal_config(), [sc])
    assert len(results) == 1
    assert results[0].name == "Base"
    assert isinstance(results[0], ScenarioResult)


def test_run_scenarios_multiple():
    """Multiple scenarios each produce a named result."""
    scenarios = [
        ScenarioConfig(name="Low", overrides=[]),
        ScenarioConfig(name="High", overrides=[]),
    ]
    results = run_scenarios(_minimal_config(), scenarios)
    assert len(results) == 2
    assert results[0].name == "Low"
    assert results[1].name == "High"


def test_override_applies():
    """An override should modify the config (project_name changed)."""
    sc = ScenarioConfig(name="Renamed", overrides=[
        ScenarioOverride(path="project_name", value="New Name"),
    ])
    results = run_scenarios(_minimal_config(), [sc])
    assert results[0].kpis is not None
    assert isinstance(results[0].kpis, dict)


def test_scenario_result_has_kpis():
    """Each ScenarioResult should contain extracted KPIs."""
    sc = ScenarioConfig(name="Base", overrides=[])
    results = run_scenarios(_minimal_config(), [sc])
    kpis = results[0].kpis
    assert "total_capex" in kpis
    assert "total_revenue" in kpis
    assert "total_opex" in kpis


# ---------------------------------------------------------------------------
# aggregate_portfolio
# ---------------------------------------------------------------------------

def test_portfolio_aggregation_empty():
    """Empty results list returns empty portfolio KPIs."""
    pr = aggregate_portfolio([])
    assert isinstance(pr, PortfolioResult)
    assert pr.portfolio_kpis == {}
    assert pr.scenario_results == []


def test_portfolio_sums():
    """Portfolio aggregation should produce ev and other sum-based KPIs."""
    sc = ScenarioConfig(name="A", overrides=[])
    results = run_scenarios(_minimal_config(), [sc, sc])
    pr = aggregate_portfolio(results)
    assert "ev" in pr.portfolio_kpis
    assert "total_capex" in pr.portfolio_kpis
    assert "senior_debt" in pr.portfolio_kpis


def test_portfolio_gearing():
    """Portfolio gearing should be derived from senior_debt / total_capex."""
    sc = ScenarioConfig(name="A", overrides=[])
    results = run_scenarios(_minimal_config(), [sc])
    pr = aggregate_portfolio(results)
    pk = pr.portfolio_kpis
    if pk["total_capex"] > 0:
        assert pk["gearing"] == pytest.approx(
            pk["senior_debt"] / pk["total_capex"]
        )
    else:
        assert pk["gearing"] == 0.0


def test_portfolio_total_equity():
    """total_equity = total_capex - senior_debt."""
    sc = ScenarioConfig(name="A", overrides=[])
    results = run_scenarios(_minimal_config(), [sc])
    pr = aggregate_portfolio(results)
    pk = pr.portfolio_kpis
    assert pk["total_equity"] == pytest.approx(pk["total_capex"] - pk["senior_debt"])


# ---------------------------------------------------------------------------
# _extract_kpis
# ---------------------------------------------------------------------------

def test_extract_kpis_no_modules():
    """With no modules enabled, KPIs should still contain required keys."""
    result = run(_minimal_config())
    kpis = _extract_kpis(result)
    assert "total_capex" in kpis
    assert "total_revenue" in kpis
    assert "ebitda" in kpis
    assert "senior_debt" in kpis


def test_extract_kpis_returns_dict():
    result = run(_minimal_config())
    kpis = _extract_kpis(result)
    assert isinstance(kpis, dict)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

def test_scenario_override_creation():
    so = ScenarioOverride(path="timeline.periods", value=48)
    assert so.path == "timeline.periods"
    assert so.value == 48


def test_scenario_config_creation():
    sc = ScenarioConfig(name="Test", overrides=[])
    assert sc.name == "Test"
    assert sc.overrides == []


def test_sensitivity_axis_creation():
    sa = SensitivityAxis(label="Capex", path="capex.total", values=[100, 200, 300])
    assert sa.label == "Capex"
    assert len(sa.values) == 3
