"""
Calibration Run #5 -- Mulwala BESS (Lancaster Risk case)

AU market, 80 MW / 4hr / 320 MWh BESS, VIC
Virtual Tolling Agreement with Macquarie

Reference model: Mulwala_PPA_Model_v1.xlsm (annual, AUD)
Library output: monthly, AUD (via currency config)

Targets (Lancaster Risk case):
  Project IRR:     10.47%
  Equity IRR:      15.21%
  Total CAPEX:     AUD 107,617,921
  EBITDA Y1 (2028): AUD 12,089,832
  Tax Y1:          AUD ~1,257,000
"""

import json
import math
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from assembly.engine import (
    ProjectConfig, TimelineConfig, StatementConfig, run
)
from assembly.excel_writer import write_workbook

import modules.revenue.REV_002 as REV_002
import modules.costs.OPEX_002 as OPEX_002
import modules.capex.CAPEX_001 as CAPEX_001
import modules.capex.BESS_REPOW_001 as BESS_REPOW_001
import modules.debt.DEBT_001 as DEBT_001
import modules.tax.TAX_AU_001 as TAX_AU_001
import modules.core.WACC_001 as WACC_001
import modules.statements.DIV_001 as DIV_001


# ============================================================================
# CONSTANTS -- Mulwala BESS (Lancaster Risk, VIC)
# ============================================================================

START_YEAR = 2025
START_MONTH = 1
N = 396                          # 33 years (Jan 2025 - Dec 2057)
COD_PERIOD = 35                  # Dec 2027 (35 months from Jan 2025)
CONSTR_START = 20                # Sep 2026 (20 months from Jan 2025)
CONSTR_MONTHS = COD_PERIOD - CONSTR_START  # 15 months

# Battery specs
POWER_MW = 80.0
DURATION_HR = 4.0
ENERGY_MWH = POWER_MW * DURATION_HR  # 320 MWh
CYCLES_PER_DAY = 1.0
AVAILABILITY = 0.99
RTE_INITIAL = 0.835              # Lancaster Risk RTE year 1

# Annual RTE curve (Lancaster Risk, from reference model)
RTE_CURVE = [
    0.8351, 0.8289, 0.8255, 0.8222, 0.8190, 0.8158, 0.8127, 0.8097,
    0.8067, 0.8038, 0.8009, 0.7981, 0.7953, 0.7926, 0.7899, 0.7873,
    0.7847, 0.7821, 0.7796, 0.7771, 0.7747, 0.7723, 0.7699, 0.7676,
    0.7653, 0.7630, 0.7608, 0.7586, 0.7564, 0.7543,
]

# Revenue
TOLLING_PRICE_PER_MW_YEAR = 164_700.0  # AUD/MW/year
TOLLING_ESCALATION = 0.025
TOLLING_CONTRACTED_PCT = 0.50     # 50% contracted
TOLLING_TENOR_YEARS = 7
INFLATION_RATE = 0.025

# Pricing (nominal AUD/MWh, calibrated from reference year 1 values)
DIS_PRICE_BASE = 95.0            # discharge price year 1 (calibrated)
CHG_PRICE_BASE = 38.0            # charge price year 1 (calibrated)
FCAS_REG_PRICE = 5.0             # FCAS regulation AUD/MWh
FCAS_CON_PRICE = 3.0             # FCAS contingency AUD/MWh

# CAPEX
CAPEX_TOTAL = 107_617_921.0      # AUD total
REPOW_YEAR = 21                  # repowering in year 21 (period 252+)
REPOW_COST = 34_437_735.0

# Debt
GEARING = 0.70
DEBT_FACILITY = CAPEX_TOTAL * GEARING  # 75,332,545
DEBT_RATE = 0.0575               # 5.75% all-in
DEBT_TENOR_YEARS = 20

# OPEX
OPEX_PER_MW_YEAR = 21_973.0     # AUD/MW/year
OPEX_ANNUAL = POWER_MW * OPEX_PER_MW_YEAR  # 1,757,840

# Tax
TAX_RATE = 0.30

# Equity
EQUITY = CAPEX_TOTAL * (1 - GEARING)  # 32,285,376


# ============================================================================
# TARGETS
# ============================================================================

TARGETS = {
    "Project IRR":  (0.1047 - 0.005, 0.1047 + 0.005),
    "Equity IRR":   (0.1521 - 0.005, 0.1521 + 0.005),
    "Total CAPEX":  (CAPEX_TOTAL * 0.99, CAPEX_TOTAL * 1.01),
    "EBITDA Y1":    (12_089_832 * 0.95, 12_089_832 * 1.05),
    "Tax Y1":       (1_257_000 * 0.90, 1_257_000 * 1.10),
}


# ============================================================================
# BUILD CURVES
# ============================================================================

def _annual_discharge_volume(n):
    """Monthly discharge MWh, degraded by RTE curve."""
    vol = [0.0] * n
    daily_discharge = ENERGY_MWH * CYCLES_PER_DAY * AVAILABILITY
    for p in range(COD_PERIOD, n):
        ops_year = (p - COD_PERIOD) // 12
        rte_idx = min(ops_year, len(RTE_CURVE) - 1)
        # Capacity scales with sqrt of RTE degradation (simplified)
        cap_factor = RTE_CURVE[rte_idx] / RTE_INITIAL
        vol[p] = daily_discharge * 30.44 * cap_factor  # avg days/month
    return vol


def _charge_volume(dis_vol, n):
    """Grid charge volume = discharge / RTE (energy needed to charge)."""
    chg = [0.0] * n
    for p in range(COD_PERIOD, n):
        ops_year = (p - COD_PERIOD) // 12
        rte_idx = min(ops_year, len(RTE_CURVE) - 1)
        rte = RTE_CURVE[rte_idx]
        chg[p] = dis_vol[p] / rte if rte > 0 else 0.0
    return chg


def _price_curve(base_price, n):
    """Monthly price with 2.5% annual escalation from model start."""
    prices = [0.0] * n
    for p in range(n):
        years = p / 12.0
        prices[p] = base_price * (1.0 + INFLATION_RATE) ** years
    return prices


# ============================================================================
# BUILD CONFIG & RUN
# ============================================================================

print("=" * 60)
print("Mulwala BESS -- Calibration Run #5")
print("=" * 60)

try:
    dis_vol = _annual_discharge_volume(N)
    chg_vol = _charge_volume(dis_vol, N)
    dis_price = _price_curve(DIS_PRICE_BASE, N)
    chg_price = _price_curve(CHG_PRICE_BASE, N)
    fcas_reg = _price_curve(FCAS_REG_PRICE, N)
    fcas_con = _price_curve(FCAS_CON_PRICE, N)

    # CAPEX drawdown: 50/50 split over construction period
    capex_dd = [0.0] * N
    for p in range(CONSTR_START, COD_PERIOD):
        capex_dd[p] = CAPEX_TOTAL / CONSTR_MONTHS

    # Debt drawdown: lump sum at construction start
    debt_dd = [0.0] * N
    debt_dd[CONSTR_START] = DEBT_FACILITY

    # Tolling: 50% of 80MW for 7 years from COD
    tolling_end = COD_PERIOD + TOLLING_TENOR_YEARS * 12 - 1

    # VTA compensation: simplified layers from reference model
    vta_vol = [0.0] * N
    vta_price = [0.0] * N
    vta_coloc_pct = [0.0] * N
    vta_cap_pct = [0.0] * N
    vta_deg_pct = [0.0] * N
    for p in range(COD_PERIOD, min(N, tolling_end + 1)):
        vta_vol[p] = dis_vol[p] * TOLLING_CONTRACTED_PCT
        vta_price[p] = dis_price[p]
        vta_coloc_pct[p] = 0.02   # 2% co-location curtailment
        ops_year = (p - COD_PERIOD) // 12
        vta_deg_pct[p] = 0.005 * ops_year  # 0.5% cumulative per year
        vta_cap_pct[p] = 0.01     # 1% capacity shortfall (MLF/DLF)

    config = ProjectConfig(
        project_name="Mulwala BESS -- Calibration Run #5",
        market="AU",
        technology="BESS",
        timeline=TimelineConfig(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            currency="AUD", currency_label="kAUD",
        ),
        rev_bess=REV_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            inflation_rate=0.0,  # prices already inflated
            power_MW=POWER_MW,
            energy_MWh_installed=ENERGY_MWH,
            round_trip_efficiency=RTE_INITIAL,
            rte_curve=RTE_CURVE,
            discharge_volume_MWh=dis_vol,
            discharge_price_DKK_per_MWh=dis_price,
            grid_charge_volume_MWh=chg_vol,
            grid_charge_price_DKK_per_MWh=chg_price,
            pv_charge_volume_MWh=[0.0] * N,
            pv_charge_price_DKK_per_MWh=[0.0] * N,
            goo_price_DKK_per_MWh=[0.0] * N,
            multimarket_pct=[0.0] * N,
            # Tolling
            tolling_active=True,
            tolling_contracted_mw=POWER_MW * TOLLING_CONTRACTED_PCT,
            tolling_price_DKK_per_mw_per_year=TOLLING_PRICE_PER_MW_YEAR,
            tolling_start_period=COD_PERIOD,
            tolling_end_period=tolling_end,
            tolling_inflation_rate=TOLLING_ESCALATION,
            tolling_inflation_start_year=START_YEAR,
            # FCAS
            fcas_active=True,
            fcas_regulation_price_per_MWh=fcas_reg,
            fcas_contingency_price_per_MWh=fcas_con,
            # VTA compensation
            vta_active=True,
            vta_contracted_discharge_MWh=vta_vol,
            vta_discharge_price_per_MWh=vta_price,
            vta_colocation_active=True,
            vta_colocation_shortfall_pct=vta_coloc_pct,
            vta_capacity_shortfall_active=True,
            vta_capacity_shortfall_pct=vta_cap_pct,
            vta_degradation_active=True,
            vta_degradation_shortfall_pct=vta_deg_pct,
        ),
        opex_bess=OPEX_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            inflation_rate=INFLATION_RATE,
            om=OPEX_002.ComponentRate(annual_DKKk=OPEX_ANNUAL / 1000.0),
            insurance=OPEX_002.ComponentRate(annual_DKKk=0.0),
            other=OPEX_002.ComponentRate(annual_DKKk=0.0),
        ),
        capex=CAPEX_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            construction_periods=CONSTR_MONTHS,
            construction_start_period=CONSTR_START,
            drawdown_profile=[1.0 / CONSTR_MONTHS] * CONSTR_MONTHS,
            epc_DKKk=CAPEX_TOTAL / 1000.0,  # kAUD
        ),
        debt=DEBT_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            facility=DEBT_FACILITY / 1000.0,  # kAUD
            all_in_rate=DEBT_RATE,
            repayment_type="annuity",
            tenor_months=DEBT_TENOR_YEARS * 12,
            drawdowns=[(d / 1000.0) for d in debt_dd],
            repayment_start_period=COD_PERIOD,
        ),
        tax_au=TAX_AU_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            tax_rate=TAX_RATE,
            ebitda=[0.0] * N,           # engine auto-wires
            interest_expense=[0.0] * N,  # engine auto-wires
            capex_by_bucket=[[0.0] * N for _ in range(7)],  # engine auto-wires
            opening_balances=[0.0] * 7,
            depreciation_lifetimes=[30, 30, 30, 30, 30, 30, 10],
        ),
        statements=StatementConfig(
            opening_cash_DKKk=0.0,
            opening_contributed_equity_DKKk=0.0,
            opening_retained_earnings_DKKk=0.0,
        ),
        div=DIV_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            cod_period=COD_PERIOD,
            net_income=[0.0] * N,
            closing_cash=[0.0] * N,
            payout_ratio=0.90,
            cash_reserve=50.0,
            payment_months=[6, 12],
            capital_reduction_active=False,
        ),
    )

    result = run(config)

except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)


# ============================================================================
# SCORECARD
# ============================================================================

print("\n" + "=" * 60)
print("SCORECARD -- Mulwala BESS (Lancaster Risk)")
print("=" * 60)

out = result.outputs
rev_out = out.get("REV_002")
opex_out = out.get("OPEX_002")
capex_out = out.get("CAPEX_001")
debt_out = out.get("DEBT_001")
tax_out = out.get("TAX_AU_001")
irr_out = out.get("IRR_001")
pl_out = out.get("PL_001")
div_out = out.get("DIV_001")

# Revenue / EBITDA (kAUD -> AUD for comparison with reference)
total_rev = sum(rev_out.net_revenue) if rev_out else 0.0
total_opex = opex_out.total_opex_lifetime if opex_out else 0.0
total_capex_kaud = sum(capex_out.total_capex_monthly) if capex_out else 0.0
ebitda_total = total_rev - total_opex
print(f"Total BESS revenue:    {total_rev:,.0f} kAUD")
print(f"Total OPEX:            {total_opex:,.0f} kAUD")
print(f"Total CAPEX:           {total_capex_kaud:,.0f} kAUD ({total_capex_kaud*1000:,.0f} AUD)")
print(f"Lifetime EBITDA:       {ebitda_total:,.0f} kAUD")

# Year 1 (2028) = periods 36-47
yr1_start, yr1_end = 36, 48
if rev_out:
    yr1_rev = sum(rev_out.net_revenue[yr1_start:yr1_end])
    yr1_opex = sum(opex_out.total_opex[yr1_start:yr1_end]) if opex_out else 0.0
    yr1_ebitda = yr1_rev - yr1_opex
    print(f"EBITDA Y1 (2028):      {yr1_ebitda:,.0f} kAUD ({yr1_ebitda*1000:,.0f} AUD)")

if debt_out:
    print(f"Debt facility:         {debt_out.opening_balance[0]:,.0f} kAUD")
    print(f"Lifetime interest:     {sum(debt_out.interest):,.0f} kAUD")
if tax_out:
    print(f"Total tax paid:        {tax_out.total_tax_paid:,.0f} kAUD")
    yr1_tax = sum(tax_out.tax_charge_accrued[yr1_start:yr1_end])
    print(f"Tax Y1 (2028):         {yr1_tax:,.0f} kAUD ({yr1_tax*1000:,.0f} AUD)")
if irr_out:
    print(f"Project IRR:           {irr_out.project_irr:.2%}")
    print(f"Equity IRR:            {irr_out.equity_irr:.2%}")
if div_out:
    print(f"Total dividends:       {div_out.total_dividends:,.0f} kAUD")

# VTA
if rev_out:
    vta = sum(rev_out.vta_total_compensation)
    fcas = sum(rev_out.fcas_total_revenue)
    tolling = sum(rev_out.tolling_revenue)
    print(f"Tolling revenue:       {tolling:,.0f} kAUD")
    print(f"FCAS revenue:          {fcas:,.0f} kAUD")
    print(f"VTA compensation:      {vta:,.0f} kAUD")


# ============================================================================
# RANGE CHECKS
# ============================================================================

print("\n--- Range Checks ---")
checks_pass = True

def _check(name, actual, lo, hi):
    global checks_pass
    if actual is None:
        print(f"  >> {name}: N/A")
        checks_pass = False
        return
    ok = lo <= actual <= hi
    marker = "  " if ok else ">>"
    if abs(actual) < 1:
        print(f"  {marker} {name}: {actual:.2%} in [{lo:.2%}, {hi:.2%}]: {'PASS' if ok else 'FAIL'}")
    else:
        print(f"  {marker} {name}: {actual:,.0f} in [{lo:,.0f}, {hi:,.0f}]: {'PASS' if ok else 'FAIL'}")
    if not ok:
        checks_pass = False

if irr_out:
    _check("Project IRR", irr_out.project_irr, *TARGETS["Project IRR"])
    _check("Equity IRR", irr_out.equity_irr, *TARGETS["Equity IRR"])
_check("Total CAPEX (AUD)", total_capex_kaud * 1000, *TARGETS["Total CAPEX"])
if rev_out:
    _check("EBITDA Y1 (AUD)", yr1_ebitda * 1000, *TARGETS["EBITDA Y1"])
if tax_out:
    _check("Tax Y1 (AUD)", yr1_tax * 1000, *TARGETS["Tax Y1"])


# ============================================================================
# GAP REPORT
# ============================================================================

print("\n--- Known Gaps ---")
gaps = [
    ("REVENUE", "Discharge/charge volumes simplified from battery specs, not hourly dispatch"),
    ("REVENUE", "Price curves use flat escalation, not Aurora VIC Central scenario"),
    ("VTA", "VTA shortfall percentages are simplified annual averages"),
    ("DEBT", "No IDC capitalisation for construction period interest"),
    ("TIMING", "Stub year 2027 (1 month ops) may differ from reference yearfrac treatment"),
]
for cat, desc in gaps:
    print(f"  [{cat}] {desc}")


# ============================================================================
# SAVE
# ============================================================================

results_path = "calibration/results/mulwala_results.json"
os.makedirs(os.path.dirname(results_path), exist_ok=True)
with open(results_path, "w") as f:
    json.dump({
        "run": "mulwala_calibration_05",
        "date": "2026-03-30",
        "project": "Mulwala BESS (Lancaster Risk)",
        "modules_run": list(out.keys()),
        "actuals": {
            "project_irr": irr_out.project_irr if irr_out else None,
            "equity_irr": irr_out.equity_irr if irr_out else None,
            "total_capex_aud": total_capex_kaud * 1000,
            "ebitda_y1_aud": yr1_ebitda * 1000 if rev_out else None,
            "tax_y1_aud": yr1_tax * 1000 if tax_out else None,
        },
        "gaps": [{"category": c, "description": d} for c, d in gaps],
    }, f, indent=2, default=str)
print(f"\nResults saved to {results_path}")

os.makedirs("outputs", exist_ok=True)
write_workbook(result, config, "outputs/mulwala_calibration_05.xlsx")
print(f"Workbook saved to outputs/mulwala_calibration_05.xlsx")

from calibration.deal_db import save_to_deal_db
save_to_deal_db(
    "Mulwala BESS", config, result, targets=TARGETS,
    metadata={"source_model": "Mulwala_PPA_Model_v1.xlsm Lancaster Risk", "run": "05"},
)

print("\n" + "=" * 60)
print(f"ALL CHECKS: {'PASS' if checks_pass else 'FAIL'}")
print(f"KNOWN GAPS: {len(gaps)}")
print("=" * 60)
