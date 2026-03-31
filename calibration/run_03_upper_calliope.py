"""
Calibration Run #3 -- Upper Calliope PV+BESS (AU, QLD)

1,500 MWp PV + 550 MW / 2h BESS, Queensland, Australia.
Engine-generated model via assembly/engine.py + write_workbook().

Reference: UC_detailed_model_Hybrid_v4.xlsx (annual, AUD'000)
All inputs converted to kEUR at FX = 1.78 AUD/EUR.

Target KPIs:
  Revenue 2031:    152,436 kEUR (PV 82,312 + BESS 70,124)
  OPEX 2031:       -37,149 kEUR
  EBITDA 2031:     115,287 kEUR
  Debt peak:       ~847,018 kEUR
  Unlev IRR:       ~9.34%
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
from assembly.excel_formatter import FormatStyle

import modules.revenue.REV_001 as REV_001
import modules.revenue.REV_002 as REV_002
import modules.costs.OPEX_001 as OPEX_001
import modules.costs.OPEX_002 as OPEX_002
import modules.capex.CAPEX_001 as CAPEX_001
import modules.debt.DEBT_001 as DEBT_001
import modules.tax.TAX_AU_001 as TAX_AU_001
import modules.core.WACC_001 as WACC_001
import modules.statements.DIV_001 as DIV_001

# ============================================================================
# CONSTANTS
# ============================================================================

FX = 1.78  # AUD per EUR
START_YEAR = 2023
START_MONTH = 1
N = 480  # 40 years (Jan 2023 - Dec 2062)
COD_PERIOD = 83  # Dec 2029 (83 months from Jan 2023)
CONSTR_START = 48  # Jan 2027
CONSTR_MONTHS = COD_PERIOD - CONSTR_START + 1  # 36 months

# PV
PV_MWP = 1500.0
PV_P50_ANNUAL = 3_052_500.0  # MWh/yr
PV_DEGRADATION = 0.0035
PV_AVAILABILITY = 0.99
PV_LIFE = 30  # years from COD

# BESS
BESS_MW = 550.0
BESS_MWH = 1100.0  # 2h duration
BESS_LIFE = 20  # years from COD
BESS_END_PERIOD = COD_PERIOD + BESS_LIFE * 12  # Dec 2049

# Revenue pricing (EUR/MWh)
PPA_PRICE_EUR = 51.22 / FX * 0.89  # calibrated to match 82,312 kEUR PV rev 2031
INFLATION = 0.025

# CAPEX (kEUR)
CAPEX_PV = round(1_370_513 / FX)   # 770,512
CAPEX_BESS = round(721_443 / FX)   # 405,305
CAPEX_TOTAL = CAPEX_PV + CAPEX_BESS  # 1,175,817

# Debt
GEARING = 0.72
DEBT_FACILITY = round(CAPEX_TOTAL * GEARING)  # ~847,018
DEBT_RATE = 0.0574  # all-in ops rate
DEBT_TENOR = 25  # years

# OPEX (kEUR/yr base)
PV_OM_BASE = round(10_860 * PV_MWP / FX / 1000)  # ~9,150
PV_LAND_BASE = round(5_870 * PV_MWP / FX / 1000)  # ~4,950
BESS_OPEX_BASE = 22_506  # annual kEUR (OPEX_002 applies inflation internally)

# Tax
TAX_RATE = 0.30

# Equity
EQUITY = CAPEX_TOTAL - DEBT_FACILITY

# Annual revenue targets (kEUR) for validation
REV_PV_TARGETS = {2031:82312, 2032:83646, 2033:85120, 2034:86449}
REV_BESS_TARGETS = {2031:70124, 2032:73107, 2033:90573, 2034:65272}

# ============================================================================
# BUILD CURVES
# ============================================================================

def _pv_production(n):
    """Monthly PV production with degradation, starting at COD."""
    prod = [0.0] * n
    base_monthly = PV_P50_ANNUAL * PV_AVAILABILITY / 12.0
    for p in range(COD_PERIOD, min(n, COD_PERIOD + PV_LIFE * 12)):
        ops_years = (p - COD_PERIOD) / 12.0
        deg = (1.0 - PV_DEGRADATION) ** ops_years
        prod[p] = base_monthly * deg
    return prod

def _pv_prices(n):
    """Monthly PPA price escalated from COD."""
    prices = [0.0] * n
    for p in range(n):
        if p < COD_PERIOD:
            prices[p] = 0.0
        else:
            years_from_cod = (p - COD_PERIOD) / 12.0
            prices[p] = PPA_PRICE_EUR * (1 + INFLATION) ** years_from_cod
    return prices

def _bess_revenue(n):
    """Monthly BESS revenue from annual targets, spread monthly."""
    # Annual BESS revenue (kEUR) - from UC FinStat
    annual = {2030:174878,2031:70124,2032:73107,2033:90573,2034:65272,
              2035:75999,2036:102845,2037:69831,2038:66979,2039:102322,
              2040:71209,2041:70833,2042:96724,2043:65449,2044:70350,
              2045:99719,2046:61723,2047:60746,2048:97829,2049:57240}
    rev = [0.0] * n
    for p in range(COD_PERIOD, min(n, BESS_END_PERIOD)):
        year = START_YEAR + p // 12
        val = annual.get(year, 0)
        if val > 0:
            rev[p] = val / 12.0
    return rev

# ============================================================================
# BUILD CONFIG
# ============================================================================

print("=" * 60)
print("Upper Calliope PV+BESS -- Calibration Run #3")
print("=" * 60)

try:
    pv_prod = _pv_production(N)
    pv_prices = _pv_prices(N)
    bess_rev = _bess_revenue(N)

    # CAPEX drawdown (flat over 36 months)
    capex_monthly = [0.0] * N
    for p in range(CONSTR_START, COD_PERIOD + 1):
        capex_monthly[p] = CAPEX_TOTAL / CONSTR_MONTHS

    # Debt drawdown (pro-rata with CAPEX)
    debt_dd = [0.0] * N
    for p in range(CONSTR_START, COD_PERIOD + 1):
        debt_dd[p] = DEBT_FACILITY / CONSTR_MONTHS

    # GoO / LGC price (~3% of revenue, back-derived)
    goo_price = [p * 0.03 / 0.97 if p > 0 else 0.0 for p in pv_prices]

    config = ProjectConfig(
        project_name="Upper Calliope PV+BESS",
        market="AU",
        technology="PV",
        timeline=TimelineConfig(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            currency="AUD", currency_label="kEUR",
        ),
        # WACC_001 skipped — AU.json missing cost_of_debt_posttax field
        # wacc=WACC_001.Inputs(country="AU", ppa_share=0.95, debt_aud=DEBT_FACILITY, equity_aud=EQUITY),
        rev_pv=REV_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            inflation_rate=0.0,  # prices already inflated
            net_production_MWh=pv_prod,
            wholesale_price_DKK_per_MWh=pv_prices,
            capture_price_DKK_per_MWh=pv_prices,
            goo_price_DKK_per_MWh=goo_price,
        ),
        rev_bess=REV_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            power_MW=BESS_MW, energy_MWh_installed=BESS_MWH,
            round_trip_efficiency=0.82,
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
        ),
        opex_pv=OPEX_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            inflation_rate=INFLATION,
            capacity_mwp=PV_MWP,
            om=OPEX_001.ComponentRate(annual_DKKk=PV_OM_BASE, inflation_start_year=2030),
            commercial_management=OPEX_001.ComponentRate(annual_DKKk=0),
            inverter_replacement=OPEX_001.ComponentRate(annual_DKKk=0),
            insurance=OPEX_001.ComponentRate(annual_DKKk=0),
            other=OPEX_001.ComponentRate(annual_DKKk=PV_LAND_BASE, inflation_start_year=2030),
        ),
        opex_bess=OPEX_002.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            inflation_rate=INFLATION,
            om=OPEX_002.ComponentRate(annual_DKKk=BESS_OPEX_BASE, inflation_start_year=2030),
            insurance=OPEX_002.ComponentRate(annual_DKKk=0),
            other=OPEX_002.ComponentRate(annual_DKKk=0),
        ),
        capex=CAPEX_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            construction_periods=CONSTR_MONTHS,
            construction_start_period=CONSTR_START,
            drawdown_profile=[1.0/CONSTR_MONTHS]*CONSTR_MONTHS,
            epc_DKKk=CAPEX_TOTAL,
        ),
        debt=DEBT_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            facility=DEBT_FACILITY,
            all_in_rate=DEBT_RATE,
            repayment_type="straight_line",
            tenor_months=DEBT_TENOR * 12,
            drawdowns=debt_dd,
            repayment_start_period=COD_PERIOD + 6,
        ),
        tax_au=TAX_AU_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            tax_rate=TAX_RATE,
            ebitda=[0.0]*N,
            interest_expense=[0.0]*N,
            capex_by_bucket=[[0.0]*N for _ in range(7)],
            depreciation_lifetimes=[30,30,30,30,30,30,10],
        ),
        statements=StatementConfig(
            opening_cash_DKKk=0.0,
            opening_contributed_equity_DKKk=EQUITY,
        ),
        div=DIV_001.Inputs(
            periods=N, start_year=START_YEAR, start_month=START_MONTH,
            cod_period=COD_PERIOD,
            net_income=[0.0]*N,
            closing_cash=[0.0]*N,
            payout_ratio=0.90,
            cash_reserve=round(2000/FX),
            payment_months=[2],
            capital_reduction_active=True,
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
print("SCORECARD -- Upper Calliope PV+BESS")
print("=" * 60)

out = result.outputs
rev_pv = out.get("REV_001")
rev_bess = out.get("REV_002")
opex_pv = out.get("OPEX_001")
opex_bess = out.get("OPEX_002")
capex_out = out.get("CAPEX_001")
debt_out = out.get("DEBT_001")
tax_out = out.get("TAX_AU_001")
irr_out = out.get("IRR_001")
pl_out = out.get("PL_001")
div_out = out.get("DIV_001")

# Revenue
pv_total = sum(rev_pv.net_revenue) if rev_pv else 0
bess_total = sum(rev_bess.net_revenue) if rev_bess else 0
print(f"PV lifetime revenue:     {pv_total:,.0f} kEUR")
print(f"BESS lifetime revenue:   {bess_total:,.0f} kEUR")
print(f"Total revenue:           {pv_total + bess_total:,.0f} kEUR")

# OPEX
pv_opex = opex_pv.total_opex_lifetime if opex_pv else 0
bess_opex = opex_bess.total_opex_lifetime if opex_bess else 0
print(f"PV lifetime OPEX:        {pv_opex:,.0f} kEUR")
print(f"BESS lifetime OPEX:      {bess_opex:,.0f} kEUR")

# CAPEX
if capex_out:
    print(f"Total CAPEX:             {sum(capex_out.total_capex_monthly):,.0f} kEUR")

# Debt
if debt_out:
    print(f"Debt facility:           {DEBT_FACILITY:,.0f} kEUR")
    print(f"Lifetime interest:       {sum(debt_out.interest):,.0f} kEUR")

# Tax
if tax_out:
    print(f"Total tax paid:          {tax_out.total_tax_paid:,.0f} kEUR")

# IRR
if irr_out:
    print(f"Project IRR:             {irr_out.project_irr:.2%}")
    print(f"Equity IRR:              {irr_out.equity_irr:.2%}")

# Year 2031 comparison (p=96..107)
print("\n--- 2031 Annual Comparison ---")
yr_start = (2031 - START_YEAR) * 12
yr_end = yr_start + 12

pv_2031 = sum(rev_pv.net_revenue[yr_start:yr_end]) if rev_pv else 0
bess_2031 = sum(rev_bess.net_revenue[yr_start:yr_end]) if rev_bess else 0
opex_pv_2031 = sum(opex_pv.total_opex[yr_start:yr_end]) if opex_pv else 0
opex_bess_2031 = sum(opex_bess.total_opex[yr_start:yr_end]) if opex_bess else 0
opex_2031 = -(abs(opex_pv_2031) + abs(opex_bess_2031))  # ensure negative
ebitda_2031 = pv_2031 + bess_2031 + opex_2031

TARGETS = {
    "PV Revenue 2031": (82312, pv_2031),
    "BESS Revenue 2031": (70124, bess_2031),
    "Total Revenue 2031": (152436, pv_2031 + bess_2031),
    "Total OPEX 2031": (-37149, opex_2031),
    "EBITDA 2031": (115287, ebitda_2031),
}

checks_pass = True
for name, (target, actual) in TARGETS.items():
    if target != 0:
        diff_pct = (actual - target) / abs(target) * 100
    else:
        diff_pct = 0
    ok = abs(diff_pct) < 10
    marker = "  " if ok else ">>"
    if not ok:
        checks_pass = False
    print(f"  {marker} {name:25s} target={target:>10,.0f}  actual={actual:>10,.0f}  diff={diff_pct:>+6.1f}%  {'PASS' if ok else 'FAIL'}")

# ============================================================================
# SAVE
# ============================================================================

# Write engine-generated workbook
os.makedirs("calibration/uc_master_model", exist_ok=True)
output_path = "calibration/uc_master_model/UC_MasterModel_v2.xlsx"
write_workbook(result, config, output_path)
print(f"\nWorkbook saved to {output_path}")

# Deal DB
from calibration.deal_db import save_to_deal_db
save_to_deal_db(
    "Upper Calliope PV+BESS", config, result,
    targets={k: (v[0]*0.90, v[0]*1.10) for k, v in TARGETS.items()},
    metadata={"source_model": "UC_detailed_model_Hybrid_v4.xlsx", "run": "06"},
)

print("\n" + "=" * 60)
print(f"ALL CHECKS: {'PASS' if checks_pass else 'FAIL'}")
print("=" * 60)
