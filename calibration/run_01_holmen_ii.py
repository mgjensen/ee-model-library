"""
Calibration Run #1 — Holmen II DK Hybrid PV+BESS
Reference: 251117_DK_BESS_FID_model_v4_38_Sebu - Holmen II
Target KPIs: Project IRR ~3.97%, Equity IRR ~4.25%, EV EUR 48,519k

This script:
  1. Builds a ProjectConfig from reference model assumptions
  2. Runs engine.run(config) and compares outputs to Dashboard targets
  3. Logs mismatches with categories: MODULE_BUG, INPUT_GAP, MISSING_FEATURE, APPROXIMATION
  4. Saves scorecard to calibration/results/holmen_ii_results.json
"""

import json
import math
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from assembly.engine import (
    ProjectConfig, TimelineConfig, TaxConfig, StatementConfig, run
)
from assembly.excel_writer import write_workbook

import modules.revenue.REV_001 as REV_001
import modules.revenue.REV_002 as REV_002
import modules.costs.OPEX_001 as OPEX_001
import modules.costs.OPEX_002 as OPEX_002
import modules.capex.CAPEX_001 as CAPEX_001
import modules.debt.DEBT_001 as DEBT_001
import modules.debt.CONSTR_FINANCE_001 as CONSTR_FINANCE_001
import modules.debt.SHL_001 as SHL_001
import modules.core.WACC_001 as WACC_001

# ============================================================================
# CONSTANTS from reference model (1.1 Input_NTD, Holmen II column)
# ============================================================================

N = 372             # 31 years monthly
START_YEAR = 2025
START_MONTH = 7     # Cut-off date Jul 2025

# PV
PV_MW = 51.03
PV_P50_ANNUAL = 52797.0          # MWh/yr
PV_DEGRADATION = 0.0025          # 0.25%/yr
PV_OPS_END = 332                 # Feb 2053

# BESS
BESS_MW = 28.0
BESS_MWH = 112.0
BESS_COD_PERIOD = 9              # Apr 2026 = 9 months from Jul 2025

# Seasonality (reference model rows 69-80)
SEASONALITY = [
    0.01296, 0.02959, 0.07864, 0.13417, 0.15622, 0.15071,
    0.14821, 0.12950, 0.08468, 0.04703, 0.01847, 0.00983
]

EU_CPI = 0.02
GOO_PRICE = 2.5  # EUR/MWh

# CAPEX (EURk)
CAPEX_TOTAL_EURk = 52_121.507

# Debt — 3 linear facilities, combined for DEBT_001
FAC1 = 13_837.568   # EURk, 17yr
FAC2 = 5_535.027     # EURk, 10yr
FAC3 = 11_900.309    # EURk, 10yr
TOTAL_FACILITY = FAC1 + FAC2 + FAC3  # 31,272.9 EURk
ALL_IN_RATE = 0.03068
REPAYMENT_START = 22  # May 2027 (1 period after drawdown at period 21)
CONSTRUCTION_MONTHS = 9

# Construction finance (bullet facility)
CF_LTV = 0.55
CF_FACILITY = CAPEX_TOTAL_EURk * CF_LTV  # 55% of 52,121.5 = ~28,667 EURk
CF_MARGIN = 0.04                          # 4.0% all-in
CF_ARRANGEMENT_FEE = 0.0025               # 0.25%
CF_START_PERIOD = 0                       # Loan starts at model start (Jul 2025)
CF_COD_PERIOD = CONSTRUCTION_MONTHS       # Bullet repayment at BESS COD (Apr 2026)

# SHL (shareholder loan)
SHL_PCT = 0.80                            # 80% of total equity+SHL
SHL_MARGIN = 0.0795                       # 7.95% PIK
# Total equity = CAPEX - senior debt - constr finance rollover
# Equity+SHL = CAPEX - senior ops debt ≈ 52,122 - 31,273 = 20,849
# SHL = 80% of 20,849 ≈ 16,679 (reference: 18,646 — difference due to IDC)
# We'll let SHL_001 compute from equity_contributed

# Opening balances (EURk)
OPENING_CASH = 170.643
OPENING_RE = 170.643
EQUITY_INJECTION = 4_753.75

# Retrofit opening balances — PV operating since Feb 2023 (2.4yr before Jul 2025)
# Fixed assets (investment basis): EUR 29,170k
# 25% declining balance for 2.4 years: acc_dep = 29170 * (1 - 0.75^2.4) ≈ 12,460
OPENING_FA_GROSS = 29_170.0    # EURk — PV fixed assets at COD
OPENING_ACC_DEP = -12_460.0    # EURk — 2.4yr of 25% declining balance (negative)

# Equity discount rate
EQUITY_DR = 0.069

# ============================================================================
# TARGETS from Dashboard
# ============================================================================

TARGETS = {
    "Project IRR":          0.03967,
    "Equity IRR":           0.04252,
    "EV (EURk)":            48_518.8,
    "Total CAPEX (EURk)":   52_121.5,
    "Senior debt ops (EURk)": 59_939.7,
    "Min DSCR (6m)":        2.727,
    "Min LLCR":             1.977,
    "Payback (years)":      17.833,
}


# ============================================================================
# BUILD CURVES
# ============================================================================

def _pv_production(n):
    production = [0.0] * n
    for p in range(min(n, PV_OPS_END + 1)):
        years_since_cod = 2.4 + p / 12.0
        degradation = (1 - PV_DEGRADATION) ** years_since_cod
        month_idx = (START_MONTH - 1 + p) % 12
        production[p] = PV_P50_ANNUAL * SEASONALITY[month_idx] * degradation
    return production


def _bess_revenue(n):
    """GAP: APPROXIMATION — flat spread of EUR 127,878k over 360 months."""
    rev = [0.0] * n
    monthly = 127_878.0 / 360.0
    for p in range(BESS_COD_PERIOD, min(BESS_COD_PERIOD + 360, n)):
        rev[p] = monthly
    return rev


# ============================================================================
# BUILD CONFIG & RUN
# ============================================================================

print("=" * 60)
print("Holmen II — Calibration Run #1")
print("=" * 60)

try:
    pv_prod = _pv_production(N)
    bess_rev = _bess_revenue(N)

    # Drawdowns: spread senior debt over construction
    drawdowns = [0.0] * N
    for p in range(CONSTRUCTION_MONTHS):
        drawdowns[p] = TOTAL_FACILITY / CONSTRUCTION_MONTHS

    # CAPEX monthly schedule for constr finance (even spread over construction)
    capex_monthly = [0.0] * N
    for p in range(CONSTRUCTION_MONTHS):
        capex_monthly[p] = CAPEX_TOTAL_EURk / CONSTRUCTION_MONTHS

    # Equity contributions: equity = CAPEX - senior debt - constr finance
    # Equity is drawn first in equity-first mode, spread over construction
    equity_total = EQUITY_INJECTION
    equity_contributed = [0.0] * N
    for p in range(CONSTRUCTION_MONTHS):
        equity_contributed[p] = equity_total / CONSTRUCTION_MONTHS

    config = ProjectConfig(
        project_name="Holmen II — Calibration Run #1",
        market="DK",
        technology="HYBRID",
        timeline=TimelineConfig(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            currency="EUR",
        ),
        wacc=WACC_001.Inputs(
            country="DK", ppa_share=0.0,
            debt_aud=TOTAL_FACILITY, equity_aud=EQUITY_INJECTION,
        ),
        rev_pv=REV_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            net_production_MWh=pv_prod,
            wholesale_price_DKK_per_MWh=[52.0] * N,
            capture_price_DKK_per_MWh=[48.0] * N,
            goo_price_DKK_per_MWh=[GOO_PRICE] * N,
        ),
        rev_bess=REV_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            power_MW=BESS_MW, energy_MWh_installed=BESS_MWH,
            round_trip_efficiency=0.82,
            discharge_volume_MWh=[0.0] * N,
            discharge_price_DKK_per_MWh=[0.0] * N,
            grid_charge_volume_MWh=[0.0] * N,
            grid_charge_price_DKK_per_MWh=[0.0] * N,
            pv_charge_volume_MWh=[0.0] * N,
            pv_charge_price_DKK_per_MWh=[0.0] * N,
            goo_price_DKK_per_MWh=[0.0] * N,
            multimarket_pct=[0.0] * N,
            external_mode=True,
            external_bess_revenue_DKKk=bess_rev,
        ),
        opex_pv=OPEX_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            capacity_mwp=PV_MW,
            om=OPEX_001.ComponentRate(annual_DKKk=56.865),
            commercial_management=OPEX_001.ComponentRate(annual_DKKk=28.507),
            inverter_replacement=OPEX_001.ComponentRate(annual_DKKk=0.0),
            insurance=OPEX_001.ComponentRate(annual_DKKk=61.325),
            other=OPEX_001.ComponentRate(annual_DKKk=155.0),
            inflation_rate=EU_CPI,
        ),
        opex_bess=OPEX_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            om=OPEX_002.ComponentRate(annual_DKKk=56.0),
            insurance=OPEX_002.ComponentRate(annual_DKKk=97.062),
            other=OPEX_002.ComponentRate(annual_DKKk=248.0),
            inflation_rate=EU_CPI,
        ),
        capex=CAPEX_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            construction_periods=CONSTRUCTION_MONTHS,
            drawdown_profile=[1.0 / CONSTRUCTION_MONTHS] * CONSTRUCTION_MONTHS,
            epc_DKKk=CAPEX_TOTAL_EURk,  # All CAPEX in one bucket (EURk)
        ),
        debt=DEBT_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            facility=TOTAL_FACILITY,
            all_in_rate=ALL_IN_RATE,
            repayment_type="straight_line",
            tenor_months=17 * 12,
            drawdowns=drawdowns,
            repayment_start_period=REPAYMENT_START,
        ),
        # Construction finance — bullet facility, repaid at COD
        constr_finance=CONSTR_FINANCE_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            facility_DKKk=CF_FACILITY,
            construction_start_period=CF_START_PERIOD,
            cod_period=CF_COD_PERIOD,
            capex_monthly=capex_monthly,
            equity_first=True,
            total_equity_DKKk=EQUITY_INJECTION,
            base_rate=0.0,           # all-in rate includes base
            margin=CF_MARGIN,        # 4.0% all-in
            arrangement_fee_pct=CF_ARRANGEMENT_FEE,
        ),

        # SHL — 80% of equity at 7.95% PIK
        shl=SHL_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            shl_pct_of_equity=SHL_PCT,
            margin=SHL_MARGIN,
            accrued=True,
            equity_contributed=equity_contributed,
        ),

        tax=TaxConfig(country="DK"),
        statements=StatementConfig(
            opening_cash_DKKk=OPENING_CASH,
            opening_contributed_equity_DKKk=EQUITY_INJECTION,
            opening_retained_earnings_DKKk=OPENING_RE,
            equity_discount_rate=EQUITY_DR,
            opening_fixed_assets_gross_DKKk=OPENING_FA_GROSS,
            opening_accumulated_depreciation_DKKk=OPENING_ACC_DEP,
        ),
    )
    print("ProjectConfig built")

except Exception as e:
    print(f"CONFIG ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Run engine
print("\nRunning engine...")
try:
    result = run(config)
    print(f"Engine completed — {len(result.outputs)} modules")
except Exception as e:
    print(f"ENGINE ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)


# ============================================================================
# SCORECARD
# ============================================================================

print("\n" + "=" * 60)
print("SCORECARD")
print("=" * 60)

out = result.outputs
mismatches = []


def fmt(v):
    if v is None: return "N/A"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1 else f"{v:,.1f}"
    return str(v)


def compare(label, actual, target, tol=10.0, cat=""):
    if actual is None or (isinstance(actual, float) and math.isnan(actual)):
        status, pct = "MISSING", None
    elif target == 0:
        status, pct = ("OK" if abs(actual) < 0.01 else "MISMATCH"), None
    else:
        pct = abs((actual - target) / target) * 100
        status = "OK" if pct <= tol else "MISMATCH"
    marker = "  " if status == "OK" else ">>"
    pct_str = f"({pct:.1f}%)" if pct is not None else ""
    print(f"  {marker} {label:35s}  actual={fmt(actual):>12s}  target={fmt(target):>12s}  {pct_str}")
    if status != "OK":
        mismatches.append({"kpi": label, "actual": actual, "target": target,
                          "pct_off": pct, "category": cat})


# Extract outputs
rev_pv = out.get("REV_001")
rev_bess = out.get("REV_002")
opex_pv = out.get("OPEX_001")
opex_bess = out.get("OPEX_002")
capex_out = out.get("CAPEX_001")
debt_out = out.get("DEBT_001")
cf_out = out.get("CONSTR_FINANCE_001")
shl_out = out.get("SHL_001")
irr_out = out.get("IRR_001")
bs_out = out.get("BS_001")

# Print summaries
if rev_pv: print(f"\nPV lifetime revenue:   {sum(rev_pv.net_revenue):,.0f} EURk")
if rev_bess: print(f"BESS lifetime revenue: {sum(rev_bess.net_revenue):,.0f} EURk")
if opex_pv: print(f"PV OPEX avg/month:     {sum(opex_pv.total_opex)/N:,.1f} EURk")
if opex_bess: print(f"BESS OPEX avg/month:   {sum(opex_bess.total_opex)/N:,.1f} EURk")
if capex_out: print(f"Total CAPEX:           {sum(capex_out.total_capex_monthly):,.1f} EURk")
if debt_out: print(f"Senior debt peak/final:{max(debt_out.closing_balance):,.1f} / {debt_out.closing_balance[-1]:,.2f} EURk")
if cf_out: print(f"Constr finance peak:   {max(cf_out.closing_balance):,.1f} EURk")
if shl_out: print(f"SHL peak balance:      {max(shl_out.closing_balance):,.1f} EURk")
if irr_out: print(f"Project IRR:           {irr_out.project_irr:.2%}")
if irr_out: print(f"Equity IRR:            {irr_out.equity_irr:.2%}")
if bs_out:
    imb = max(abs(bs_out.total_assets[p] - bs_out.total_liabilities_equity[p]) for p in range(N))
    print(f"BS max imbalance:      {imb:,.2f} EURk")

# KPI comparisons
print("\n--- KPI Comparisons ---")
if irr_out:
    compare("Project IRR", irr_out.project_irr, TARGETS["Project IRR"], tol=50, cat="INPUT_GAP")
    compare("Equity IRR", irr_out.equity_irr, TARGETS["Equity IRR"], tol=50, cat="INPUT_GAP")
if capex_out:
    compare("Total CAPEX (EURk)", sum(capex_out.total_capex_monthly),
            TARGETS["Total CAPEX (EURk)"], tol=5, cat="APPROXIMATION")
if debt_out:
    compare("Debt peak (EURk)", max(debt_out.closing_balance),
            TARGETS["Senior debt ops (EURk)"], tol=50, cat="MISSING_FEATURE")
    compare("Debt repaid", 1.0 if debt_out.closing_balance[-1] < 1.0 else 0.0,
            1.0, tol=0, cat="MODULE_BUG")

# Known gaps
gaps = [
    ("MISSING_FEATURE", "3 linear facilities combined into 1 DEBT_001. Need DEBT_LINEAR_001 multi-facility."),
    ("MISSING_FEATURE", "No VAT facility (VAT_FACILITY_001 not wired)."),
    # FIXED: BS_001 now accepts opening_fixed_assets_gross and opening_accumulated_depreciation
    ("APPROXIMATION", "BESS revenue flat EUR 355k/month. Real = Aurora tech model curves."),
    ("APPROXIMATION", "PV capture price flat EUR 48/MWh. Real = Aurora modified curves."),
    ("APPROXIMATION", "PV/BESS OPEX lumped into 4 buckets (reference has 15+ line items)."),
    ("APPROXIMATION", "Equity contribution spread evenly. Real = equity-first sequencing."),
    ("INPUT_GAP", "Currency EUR but field names say DKKk. No FX conversion."),
]

print("\n--- Known Gaps ---")
for cat, desc in gaps:
    print(f"  [{cat}] {desc}")
    mismatches.append({"kpi": "GAP", "category": cat, "detail": desc})

# Save results
results_path = "calibration/results/holmen_ii_results.json"
os.makedirs(os.path.dirname(results_path), exist_ok=True)
results = {
    "run": "holmen_ii_calibration_01",
    "date": "2026-03-28",
    "reference": "251117_DK_BESS_FID_model_v4_38_Sebu_Holmen_II",
    "modules_run": list(out.keys()),
    "targets": {k: v for k, v in TARGETS.items()},
    "actuals": {
        "project_irr": irr_out.project_irr if irr_out else None,
        "equity_irr": irr_out.equity_irr if irr_out else None,
        "total_capex_eurk": sum(capex_out.total_capex_monthly) if capex_out else None,
        "debt_peak_eurk": max(debt_out.closing_balance) if debt_out else None,
        "pv_revenue_eurk": sum(rev_pv.net_revenue) if rev_pv else None,
        "bess_revenue_eurk": sum(rev_bess.net_revenue) if rev_bess else None,
    },
    "mismatches": mismatches,
    "warnings": result.warnings,
}
with open(results_path, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nResults saved to {results_path}")

# Write workbook
os.makedirs("outputs", exist_ok=True)
write_workbook(result, config, "outputs/holmen_ii_calibration_01.xlsx")
print(f"Workbook saved to outputs/holmen_ii_calibration_01.xlsx")

# Final summary
print("\n" + "=" * 60)
kpi_mm = [m for m in mismatches if m.get("kpi") != "GAP"]
gap_ct = len([m for m in mismatches if m.get("kpi") == "GAP"])
print(f"KPI MISMATCHES: {len(kpi_mm)} | KNOWN GAPS: {gap_ct} | WARNINGS: {len(result.warnings)}")
print("=" * 60)
