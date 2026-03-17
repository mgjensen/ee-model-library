"""
assembly/cell_mapper.py

Maps module output fields to Excel cell references in the standard 5-sheet workbook layout.

Sheet layout:
    "Revenue"    — REV_001, REV_002
    "Costs"      — OPEX_001, OPEX_002, CAPEX_001
    "Debt"       — DEBT_001, TAX_001, WACC_001 (scalars in col B)
    "Statements" — PL_001, CF_001, BS_001, IRR_001

Column conventions (1-based):
    Col 1 (A) = field label
    Col 2 (B) = units string or scalar assumption value
    Col 3 (C) = period 0
    Col 4 (D) = period 1
    ...

Row conventions:
    Row 1 = project name / sheet title
    Row 2 = blank
    Row 3 = period indices (0, 1, 2, ...)
    Row 4 = date labels (Jan-2026, Feb-2026, ...)
    Row 5+ = data rows per the ROW_MAP below
"""

from __future__ import annotations


# ============================================================================
# MODULE → SHEET MAPPING
# ============================================================================

MODULE_SHEET: dict[str, str] = {
    "WACC_001":  "Debt",
    "REV_001":   "Revenue",
    "REV_002":   "Revenue",
    "REV_003":   "Revenue",
    "OPEX_001":  "Costs",
    "OPEX_002":  "Costs",
    "OPEX_003":  "Costs",
    "CAPEX_001": "Costs",
    "BESS_REPOW_001": "Costs",
    "DEBT_001":  "Debt",
    "DEBT_SCULPT_001": "Debt",
    "SHL_001":   "Debt",
    "VAT_FACILITY_001": "Debt",
    "DSRA_001":  "Debt",
    "DEBT_REFI_001": "Debt",
    "DEBT_LINEAR_001": "Debt",
    "CONSTR_FINANCE_001": "Debt",
    "TAX_001":   "Debt",
    "TAX_DE_001": "Debt",
    "PL_001":    "Statements",
    "CF_001":    "Statements",
    "BS_001":    "Statements",
    "IRR_001":   "Statements",
    "PRICE_CURVES_001": "Revenue",
    "WORKING_CAPITAL_001": "Statements",
    "PPA_CFD_001": "Revenue",
    "SOURCES_USES_001": "Statements",
    "MODEL_CHECKS_001": "Statements",
    "DASHBOARD_001": "Statements",
    "REPOW_DEBT_001": "Debt",
    "VALUATION_001": "Statements",
    "BREAKEVEN_001": "Statements",
}

# Modules whose outputs go to the Summary sheet (scalars only)
SUMMARY_SCALAR_MODULES = {"WACC_001", "IRR_001"}


# ============================================================================
# ROW MAP  (sheet, module_id, field_name) → row_number  (1-based)
# ============================================================================

ROW_MAP: dict[tuple[str, str, str], int] = {

    # ---- Revenue sheet — REV_001 (rows 5–27) --------------------------------
    ("Revenue", "REV_001", "net_production_MWh"):          6,
    ("Revenue", "REV_001", "price_indexation_factor"):     7,
    ("Revenue", "REV_001", "wholesale_inflated"):          8,
    ("Revenue", "REV_001", "capture_inflated"):            9,
    ("Revenue", "REV_001", "spot_revenue"):               10,
    ("Revenue", "REV_001", "total_ppa_contracted_volume"):11,
    ("Revenue", "REV_001", "total_ppa_fixed_payment"):    12,
    ("Revenue", "REV_001", "total_ppa_settlement"):       13,
    ("Revenue", "REV_001", "total_ppa_net_settlement"):   14,
    ("Revenue", "REV_001", "total_ppa_capture_compensation"): 15,
    ("Revenue", "REV_001", "total_ppa_revenue"):          16,
    ("Revenue", "REV_001", "goo_volume"):                 17,
    ("Revenue", "REV_001", "goo_revenue"):                18,
    ("Revenue", "REV_001", "tso_cost"):                   19,
    ("Revenue", "REV_001", "dso_cost"):                   20,
    ("Revenue", "REV_001", "nordpool_cost"):              21,
    ("Revenue", "REV_001", "ppa_management_cost"):        22,
    ("Revenue", "REV_001", "total_production_costs"):     23,
    ("Revenue", "REV_001", "balancing_cost"):             24,
    ("Revenue", "REV_001", "gross_revenue"):              25,
    ("Revenue", "REV_001", "total_costs"):                26,
    ("Revenue", "REV_001", "net_revenue"):                27,

    # ---- Revenue sheet — REV_002 (rows 29–57) -------------------------------
    ("Revenue", "REV_002", "discharge_volume"):                 30,
    ("Revenue", "REV_002", "discharge_indexation_factor"):      31,
    ("Revenue", "REV_002", "discharge_price_inflated"):         32,
    ("Revenue", "REV_002", "discharge_revenue"):                33,
    ("Revenue", "REV_002", "charge_indexation_factor"):         34,
    ("Revenue", "REV_002", "grid_charge_price_inflated"):       35,
    ("Revenue", "REV_002", "pv_charge_price_inflated"):         36,
    ("Revenue", "REV_002", "grid_charging_cost"):               37,
    ("Revenue", "REV_002", "pv_charging_cost"):                 38,
    ("Revenue", "REV_002", "total_charging_cost"):              39,
    ("Revenue", "REV_002", "feed_in_tariff_cost"):              40,
    ("Revenue", "REV_002", "net_loss_tariff_cost"):             41,
    ("Revenue", "REV_002", "system_tariff_cost"):               42,
    ("Revenue", "REV_002", "grid_import_fee"):                  43,
    ("Revenue", "REV_002", "total_import_production_costs"):    44,
    ("Revenue", "REV_002", "tso_export_cost"):                  45,
    ("Revenue", "REV_002", "dso_export_cost"):                  46,
    ("Revenue", "REV_002", "nordpool_export_cost"):             47,
    ("Revenue", "REV_002", "total_export_production_costs"):    48,
    ("Revenue", "REV_002", "goo_lost_volume"):                  49,
    ("Revenue", "REV_002", "goo_revenue_lost"):                 50,
    ("Revenue", "REV_002", "export_tariff_addback"):            51,
    ("Revenue", "REV_002", "total_system_adjustments"):         52,
    ("Revenue", "REV_002", "bess_net_ex_multimarket"):          53,
    ("Revenue", "REV_002", "multimarket_revenue"):              54,
    ("Revenue", "REV_002", "tolling_revenue"):                   55,
    ("Revenue", "REV_002", "gross_revenue"):                    56,
    ("Revenue", "REV_002", "total_costs"):                      57,
    ("Revenue", "REV_002", "net_revenue"):                      58,

    # ---- Revenue sheet — REV_003 (rows 60–82) — Wind revenue ---------------
    ("Revenue", "REV_003", "net_production_MWh"):          61,
    ("Revenue", "REV_003", "price_indexation_factor"):     62,
    ("Revenue", "REV_003", "wholesale_inflated"):          63,
    ("Revenue", "REV_003", "capture_inflated"):            64,
    ("Revenue", "REV_003", "spot_revenue"):                65,
    ("Revenue", "REV_003", "total_ppa_contracted_volume"): 66,
    ("Revenue", "REV_003", "total_ppa_fixed_payment"):     67,
    ("Revenue", "REV_003", "total_ppa_settlement"):        68,
    ("Revenue", "REV_003", "total_ppa_net_settlement"):    69,
    ("Revenue", "REV_003", "total_ppa_capture_compensation"): 70,
    ("Revenue", "REV_003", "total_ppa_revenue"):           71,
    ("Revenue", "REV_003", "goo_volume"):                  72,
    ("Revenue", "REV_003", "goo_revenue"):                 73,
    ("Revenue", "REV_003", "tso_cost"):                    74,
    ("Revenue", "REV_003", "dso_cost"):                    75,
    ("Revenue", "REV_003", "nordpool_cost"):               76,
    ("Revenue", "REV_003", "ppa_management_cost"):         77,
    ("Revenue", "REV_003", "total_production_costs"):      78,
    ("Revenue", "REV_003", "balancing_cost"):              79,
    ("Revenue", "REV_003", "gross_revenue"):               80,
    ("Revenue", "REV_003", "total_costs"):                 81,
    ("Revenue", "REV_003", "net_revenue"):                 82,

    # ---- Revenue sheet — PPA_CFD_001 (rows 84–86) ----------------------------
    ("Revenue", "PPA_CFD_001", "cfd_flag"):              84,
    ("Revenue", "PPA_CFD_001", "settlement_value"):       85,
    ("Revenue", "PPA_CFD_001", "strike_price_indexed"):   86,

    # ---- Costs sheet — OPEX_001 (rows 5–17) ---------------------------------
    ("Costs", "OPEX_001", "om"):                      6,
    ("Costs", "OPEX_001", "commercial_management"):   7,
    ("Costs", "OPEX_001", "inverter_replacement"):    8,
    ("Costs", "OPEX_001", "land_lease_rented"):       9,
    ("Costs", "OPEX_001", "land_lease_owned"):       10,
    ("Costs", "OPEX_001", "lease_tax"):              11,
    ("Costs", "OPEX_001", "insurance"):              12,
    ("Costs", "OPEX_001", "guarantee"):              13,
    ("Costs", "OPEX_001", "ve_bonus"):               14,
    ("Costs", "OPEX_001", "self_consumption"):       15,
    ("Costs", "OPEX_001", "other"):                  16,
    ("Costs", "OPEX_001", "total_opex"):             17,

    # ---- Costs sheet — OPEX_002 (rows 19–24) --------------------------------
    ("Costs", "OPEX_002", "om"):           20,
    ("Costs", "OPEX_002", "insurance"):   21,
    ("Costs", "OPEX_002", "trading_costs"): 22,
    ("Costs", "OPEX_002", "other"):        23,
    ("Costs", "OPEX_002", "total_opex"):   24,

    # ---- Costs sheet — OPEX_003 (rows 36–42) — Wind OPEX -------------------
    ("Costs", "OPEX_003", "om"):          37,
    ("Costs", "OPEX_003", "land_lease"):  38,
    ("Costs", "OPEX_003", "insurance"):   39,
    ("Costs", "OPEX_003", "grid_costs"):  40,
    ("Costs", "OPEX_003", "other"):       41,
    ("Costs", "OPEX_003", "total_opex"):  42,

    # ---- Costs sheet — CAPEX_001 (rows 26–34) --------------------------------
    ("Costs", "CAPEX_001", "epc"):                 27,
    ("Costs", "CAPEX_001", "grid_connection"):     28,
    ("Costs", "CAPEX_001", "development"):         29,
    ("Costs", "CAPEX_001", "land"):                30,
    ("Costs", "CAPEX_001", "contingency"):         31,
    ("Costs", "CAPEX_001", "other"):               32,
    ("Costs", "CAPEX_001", "total_capex_monthly"): 33,
    ("Costs", "CAPEX_001", "cumulative_capex"):    34,

    # ---- Costs sheet — BESS_REPOW_001 (rows 44–50) --------------------------
    ("Costs", "BESS_REPOW_001", "repowering_cost_monthly"):          44,
    ("Costs", "BESS_REPOW_001", "pre_repowering_flag"):              45,
    ("Costs", "BESS_REPOW_001", "post_repowering_flag"):             46,
    ("Costs", "BESS_REPOW_001", "tax_depreciation_monthly"):         47,
    ("Costs", "BESS_REPOW_001", "tax_asset_base_eop"):               48,
    ("Costs", "BESS_REPOW_001", "accounting_depreciation_monthly"):  49,
    ("Costs", "BESS_REPOW_001", "accounting_asset_base_eop"):        50,

    # ---- Debt sheet — DEBT_001 (rows 5–13) ----------------------------------
    ("Debt", "DEBT_001", "opening_balance"):  6,
    ("Debt", "DEBT_001", "drawdown"):         7,
    ("Debt", "DEBT_001", "interest"):         8,
    ("Debt", "DEBT_001", "principal"):        9,
    ("Debt", "DEBT_001", "cash_sweep"):      10,
    ("Debt", "DEBT_001", "closing_balance"): 11,
    ("Debt", "DEBT_001", "debt_service"):    12,
    ("Debt", "DEBT_001", "dscr_annual"):     13,

    # ---- Debt sheet — DEBT_SCULPT_001 (rows 45–52) --------------------------
    ("Debt", "DEBT_SCULPT_001", "opening_balance"):      46,
    ("Debt", "DEBT_SCULPT_001", "drawdown"):             47,
    ("Debt", "DEBT_SCULPT_001", "interest_paid"):        48,
    ("Debt", "DEBT_SCULPT_001", "principal"):             49,
    ("Debt", "DEBT_SCULPT_001", "closing_balance"):      50,
    ("Debt", "DEBT_SCULPT_001", "debt_service"):         51,
    ("Debt", "DEBT_SCULPT_001", "dscr_achieved"):        52,
    ("Debt", "DEBT_SCULPT_001", "cash_sweep"):           53,
    ("Debt", "DEBT_SCULPT_001", "llcr_series"):          54,
    ("Debt", "DEBT_SCULPT_001", "pv_cfads"):             55,

    # ---- Debt sheet — DSRA_001 (rows 57–62) ----------------------------------
    ("Debt", "DSRA_001", "target_balance"):   57,
    ("Debt", "DSRA_001", "actual_balance"):   58,
    ("Debt", "DSRA_001", "top_up"):           59,
    ("Debt", "DSRA_001", "release"):          60,
    ("Debt", "DSRA_001", "commitment_fee"):   61,
    ("Debt", "DSRA_001", "net_cash_impact"):  62,

    # ---- Debt sheet — DEBT_REFI_001 (rows 64–72) ----------------------------
    ("Debt", "DEBT_REFI_001", "opening_balance"):  64,
    ("Debt", "DEBT_REFI_001", "drawdown"):          65,
    ("Debt", "DEBT_REFI_001", "interest"):           66,
    ("Debt", "DEBT_REFI_001", "principal"):          67,
    ("Debt", "DEBT_REFI_001", "cash_sweep"):         68,
    ("Debt", "DEBT_REFI_001", "closing_balance"):   69,
    ("Debt", "DEBT_REFI_001", "debt_service"):       70,
    ("Debt", "DEBT_REFI_001", "dscr_achieved"):      71,
    ("Debt", "DEBT_REFI_001", "llcr"):               72,

    # ---- Debt sheet — DEBT_LINEAR_001 (rows 74–80) ---------------------------
    ("Debt", "DEBT_LINEAR_001", "total_opening_balance"):   74,
    ("Debt", "DEBT_LINEAR_001", "total_drawdown"):           75,
    ("Debt", "DEBT_LINEAR_001", "total_interest"):           76,
    ("Debt", "DEBT_LINEAR_001", "total_principal"):          77,
    ("Debt", "DEBT_LINEAR_001", "total_closing_balance"):   78,
    ("Debt", "DEBT_LINEAR_001", "total_debt_service"):       79,
    ("Debt", "DEBT_LINEAR_001", "total_arrangement_fees"):   80,

    # ---- Debt sheet — CONSTR_FINANCE_001 (rows 82–89) ------------------------
    ("Debt", "CONSTR_FINANCE_001", "opening_balance"):   82,
    ("Debt", "CONSTR_FINANCE_001", "drawdown"):           83,
    ("Debt", "CONSTR_FINANCE_001", "repayment"):          84,
    ("Debt", "CONSTR_FINANCE_001", "closing_balance"):   85,
    ("Debt", "CONSTR_FINANCE_001", "interest"):           86,
    ("Debt", "CONSTR_FINANCE_001", "commitment_fee"):     87,
    ("Debt", "CONSTR_FINANCE_001", "equity_drawn"):       88,
    ("Debt", "CONSTR_FINANCE_001", "idc_cumulative"):     89,

    # ---- Debt sheet — VAT_FACILITY_001 (rows 38–42) -------------------------
    ("Debt", "VAT_FACILITY_001", "vat_paid"):            39,
    ("Debt", "VAT_FACILITY_001", "vat_refund"):          40,
    ("Debt", "VAT_FACILITY_001", "facility_balance"):    41,
    ("Debt", "VAT_FACILITY_001", "interest"):            42,
    ("Debt", "VAT_FACILITY_001", "commitment_fee_paid"): 43,

    # ---- Debt sheet — SHL_001 (rows 32–36) -----------------------------------
    ("Debt", "SHL_001", "opening_balance"):  33,
    ("Debt", "SHL_001", "interest"):         34,
    ("Debt", "SHL_001", "repayment"):        35,
    ("Debt", "SHL_001", "closing_balance"):  36,

    # ---- Debt sheet — TAX_001 (rows 14–19) ----------------------------------
    ("Debt", "TAX_001", "tax_depreciation"):          15,
    ("Debt", "TAX_001", "deductible_interest"):       16,
    ("Debt", "TAX_001", "non_deductible_interest"):   17,
    ("Debt", "TAX_001", "tax_charge_accrued"):        18,
    ("Debt", "TAX_001", "tax_paid"):                  19,

    # ---- Debt sheet — TAX_DE_001 (rows 91–97) ---------------------------------
    ("Debt", "TAX_DE_001", "tax_depreciation"):        91,
    ("Debt", "TAX_DE_001", "deductible_interest"):     92,
    ("Debt", "TAX_DE_001", "non_deductible_interest"): 93,
    ("Debt", "TAX_DE_001", "corporate_tax_charge"):    94,
    ("Debt", "TAX_DE_001", "trade_tax_charge"):        95,
    ("Debt", "TAX_DE_001", "tax_charge_accrued"):      96,
    ("Debt", "TAX_DE_001", "tax_paid"):                97,

    # ---- Debt sheet — WACC_001 scalars (col B, rows 22–30) -----------------
    # These are written to col B only (no time-series). Row numbers are the same
    # convention but the writer uses col 2 (B) instead of the period columns.
    ("Debt", "WACC_001", "wacc"):                    22,
    ("Debt", "WACC_001", "blended_cost_of_equity"):  23,
    ("Debt", "WACC_001", "ppa_cost_of_equity"):      24,
    ("Debt", "WACC_001", "merchant_cost_of_equity"): 25,
    ("Debt", "WACC_001", "cost_of_debt_posttax"):    26,
    ("Debt", "WACC_001", "levered_beta"):            27,
    ("Debt", "WACC_001", "debt_to_equity_ratio"):    28,
    ("Debt", "WACC_001", "ppa_erp"):                 29,
    ("Debt", "WACC_001", "merchant_erp"):            30,

    # ---- Statements sheet — PL_001 (rows 5–14) ------------------------------
    ("Statements", "PL_001", "gross_revenue"):    6,
    ("Statements", "PL_001", "total_opex"):       7,
    ("Statements", "PL_001", "ebitda"):           8,
    ("Statements", "PL_001", "depreciation"):     9,
    ("Statements", "PL_001", "ebit"):            10,
    ("Statements", "PL_001", "interest_expense"):11,
    ("Statements", "PL_001", "ebt"):             12,
    ("Statements", "PL_001", "tax_charge"):      13,
    ("Statements", "PL_001", "net_income"):      14,

    # ---- Statements sheet — CF_001 (rows 16–32) -----------------------------
    ("Statements", "CF_001", "net_income"):             16,
    ("Statements", "CF_001", "depreciation"):           17,
    ("Statements", "CF_001", "working_capital_change"): 18,
    ("Statements", "CF_001", "cfo"):                    19,
    ("Statements", "CF_001", "capex_monthly"):          21,
    ("Statements", "CF_001", "cfi"):                    22,
    ("Statements", "CF_001", "debt_drawdown"):          24,
    ("Statements", "CF_001", "principal_repayment"):    25,
    ("Statements", "CF_001", "interest_paid"):          26,
    ("Statements", "CF_001", "dividends_paid"):         27,
    ("Statements", "CF_001", "cff"):                    28,
    ("Statements", "CF_001", "net_cash_flow"):          30,
    ("Statements", "CF_001", "opening_cash"):           31,
    ("Statements", "CF_001", "closing_cash"):           32,

    # ---- Statements sheet — BS_001 (rows 34–45) -----------------------------
    ("Statements", "BS_001", "fixed_assets_gross"):         34,
    ("Statements", "BS_001", "accumulated_depreciation"):   35,
    ("Statements", "BS_001", "fixed_assets_net"):           36,
    ("Statements", "BS_001", "cash"):                       37,
    ("Statements", "BS_001", "total_assets"):               38,
    ("Statements", "BS_001", "debt_balance"):               40,
    ("Statements", "BS_001", "contributed_equity"):         41,
    ("Statements", "BS_001", "retained_earnings"):          42,
    ("Statements", "BS_001", "total_equity"):               43,
    ("Statements", "BS_001", "total_liabilities_equity"):   44,
    ("Statements", "BS_001", "imbalance"):                  45,

    # ---- Statements sheet — IRR_001 (row 47 — time-series only) ------------
    ("Statements", "IRR_001", "cumulative_pfcf"):  47,

    # ---- Statements sheet — WORKING_CAPITAL_001 (rows 49–52) -----------------
    ("Statements", "WORKING_CAPITAL_001", "total_receivables"):       49,
    ("Statements", "WORKING_CAPITAL_001", "total_payables"):          50,
    ("Statements", "WORKING_CAPITAL_001", "working_capital_balance"): 51,
    ("Statements", "WORKING_CAPITAL_001", "working_capital_change"):  52,

    # ---- Statements sheet — SOURCES_USES_001 (rows 54–57) --------------------
    ("Statements", "SOURCES_USES_001", "equity_monthly"):     54,

    # ---- Statements sheet — VALUATION_001 (row 56) ---------------------------
    ("Statements", "VALUATION_001", "ev"):  56,

    # ---- Statements sheet — BREAKEVEN_001 (row 58) ---------------------------
    ("Statements", "BREAKEVEN_001", "breakeven_price_monthly"):  58,

    # ---- Debt sheet — REPOW_DEBT_001 (rows 99–105) --------------------------
    ("Debt", "REPOW_DEBT_001", "opening_balance"):   99,
    ("Debt", "REPOW_DEBT_001", "drawdown"):         100,
    ("Debt", "REPOW_DEBT_001", "principal"):        101,
    ("Debt", "REPOW_DEBT_001", "interest"):         102,
    ("Debt", "REPOW_DEBT_001", "closing_balance"):  103,
    ("Debt", "REPOW_DEBT_001", "debt_service"):     104,
    ("Debt", "REPOW_DEBT_001", "arrangement_fee"):  105,
}


# ============================================================================
# FIELD METADATA
# ============================================================================

FIELD_LABELS: dict[str, str] = {
    # REV_001
    "net_production_MWh":             "Net Production",
    "price_indexation_factor":        "Price Indexation Factor",
    "wholesale_inflated":             "Wholesale Price (inflated)",
    "capture_inflated":               "Capture Price (inflated)",
    "spot_revenue":                   "Spot Revenue",
    "total_ppa_contracted_volume":    "PPA Contracted Volume",
    "total_ppa_fixed_payment":        "PPA Fixed Payment",
    "total_ppa_settlement":           "PPA Settlement",
    "total_ppa_net_settlement":       "PPA Net Settlement",
    "total_ppa_capture_compensation": "PPA Capture Compensation",
    "total_ppa_revenue":              "Total PPA Revenue",
    "goo_volume":                     "GoO Volume",
    "goo_revenue":                    "GoO Revenue",
    "tso_cost":                       "TSO Cost",
    "dso_cost":                       "DSO Cost",
    "nordpool_cost":                  "Nord Pool Cost",
    "ppa_management_cost":            "PPA Management Cost",
    "total_production_costs":         "Total Production Costs",
    "balancing_cost":                 "Balancing Cost",
    # REV_002
    "discharge_volume":               "Discharge Volume",
    "discharge_indexation_factor":    "Discharge Indexation Factor",
    "discharge_price_inflated":       "Discharge Price (inflated)",
    "discharge_revenue":              "Discharge Revenue",
    "charge_indexation_factor":       "Charge Indexation Factor",
    "grid_charge_price_inflated":     "Grid Charge Price (inflated)",
    "pv_charge_price_inflated":       "PV Charge Price (inflated)",
    "grid_charging_cost":             "Grid Charging Cost",
    "pv_charging_cost":               "PV Charging Cost",
    "total_charging_cost":            "Total Charging Cost",
    "feed_in_tariff_cost":            "Feed-In Tariff Cost",
    "net_loss_tariff_cost":           "Net Loss Tariff Cost",
    "system_tariff_cost":             "System Tariff Cost",
    "grid_import_fee":                "Grid Import Fee",
    "total_import_production_costs":  "Total Import Production Costs",
    "tso_export_cost":                "TSO Export Cost",
    "dso_export_cost":                "DSO Export Cost",
    "nordpool_export_cost":           "Nord Pool Export Cost",
    "total_export_production_costs":  "Total Export Production Costs",
    "goo_lost_volume":                "GoO Lost Volume",
    "goo_revenue_lost":               "GoO Revenue Lost",
    "export_tariff_addback":          "Export Tariff Add-Back",
    "total_system_adjustments":       "Total System Adjustments",
    "bess_net_ex_multimarket":        "BESS Net (ex-Multimarket)",
    "multimarket_revenue":            "Multimarket Revenue",
    "tolling_revenue":                "Tolling Revenue",
    # Shared
    "gross_revenue":                  "Gross Revenue",
    "total_costs":                    "Total Costs",
    "net_revenue":                    "Net Revenue",
    # OPEX_001
    "om":                       "O&M",
    "commercial_management":    "Commercial Management",
    "inverter_replacement":     "Inverter Replacement",
    "land_lease_rented":        "Land Lease (rented)",
    "land_lease_owned":         "Land Lease (owned)",
    "lease_tax":                "Lease Tax",
    "insurance":                "Insurance",
    "guarantee":                "Bank Guarantee",
    "ve_bonus":                 "VE-Bonus",
    "self_consumption":         "Self-Consumption",
    "other":                    "Other",
    "total_opex":               "Total OPEX",
    "trading_costs":            "Trading Costs",
    "land_lease":               "Land Lease",
    "grid_costs":               "Grid Costs",
    # CAPEX_001
    "epc":                 "EPC / Construction",
    "grid_connection":     "Grid Connection",
    "development":         "Development & Permitting",
    "land":                "Land Acquisition",
    "contingency":         "Contingency",
    "total_capex_monthly": "Total CAPEX (monthly)",
    "cumulative_capex":    "Cumulative CAPEX",
    # BESS_REPOW_001
    "repowering_cost_monthly":          "BESS Repowering Cost",
    "pre_repowering_flag":              "Pre-Repowering Flag",
    "post_repowering_flag":             "Post-Repowering Flag",
    "tax_depreciation_monthly":         "Tax Depreciation (repowering)",
    "tax_asset_base_eop":               "Tax Asset Base EoP (repowering)",
    "accounting_depreciation_monthly":  "Accounting Depreciation (repowering)",
    "accounting_asset_base_eop":        "Accounting Asset Base EoP (repowering)",
    # DEBT_001
    "opening_balance":  "Opening Balance",
    "drawdown":         "Drawdown",
    "interest":         "Interest",
    "principal":        "Principal Repayment",
    "cash_sweep":       "Cash Sweep Prepayment",
    "closing_balance":  "Closing Balance",
    "debt_service":     "Debt Service",
    "dscr_annual":      "DSCR (annual)",
    # VAT_FACILITY_001
    "vat_paid":             "VAT Paid",
    "vat_refund":           "VAT Refund",
    "facility_balance":     "VAT Facility Balance",
    "commitment_fee_paid":  "Commitment Fee",
    # SHL_001
    "repayment":        "SHL Repayment",
    # CONSTR_FINANCE_001
    "equity_drawn":           "Equity Drawn",
    "idc_cumulative":         "IDC Cumulative",
    # DEBT_LINEAR_001
    "total_opening_balance":  "Total Opening Balance",
    "total_drawdown":         "Total Drawdown",
    "total_closing_balance":  "Total Closing Balance",
    "total_debt_service":     "Total Debt Service",
    "total_arrangement_fees": "Total Arrangement Fees",
    # DEBT_SCULPT_001 / DEBT_REFI_001
    "dscr_achieved":    "DSCR (achieved)",
    "llcr_series":      "LLCR",
    "pv_cfads":         "PV of CFADS",
    # DSRA_001
    "target_balance":   "DSRA Target Balance",
    "actual_balance":   "DSRA Actual Balance",
    "top_up":           "DSRA Top-Up",
    "release":          "DSRA Release",
    "commitment_fee":   "DSRF Commitment Fee",
    "net_cash_impact":  "DSRA Net Cash Impact",
    # TAX_001
    "tax_depreciation":         "Tax Depreciation",
    "deductible_interest":      "Deductible Interest",
    "non_deductible_interest":  "Non-Deductible Interest",
    "tax_charge_accrued":       "Tax Charge (accrued)",
    "tax_paid":                 "Tax Paid",
    "corporate_tax_charge":     "Corporate Tax (CIT)",
    "trade_tax_charge":         "Trade Tax (Gewerbesteuer)",
    # WACC_001
    "wacc":                   "WACC",
    "blended_cost_of_equity": "Blended CoE",
    "ppa_cost_of_equity":     "PPA CoE",
    "merchant_cost_of_equity":"Merchant CoE",
    "cost_of_debt_posttax":   "Cost of Debt (post-tax)",
    "levered_beta":           "Levered Beta",
    "debt_to_equity_ratio":   "Debt/Equity Ratio",
    "ppa_erp":                "PPA ERP",
    "merchant_erp":           "Merchant ERP",
    # PL_001
    "ebitda":            "EBITDA",
    "depreciation":      "Depreciation & Amortisation",
    "ebit":              "EBIT",
    "interest_expense":  "Interest Expense",
    "ebt":               "EBT",
    "tax_charge":        "Tax Charge",
    "net_income":        "Net Income",
    # CF_001
    "working_capital_change": "Working Capital Change",
    "cfo":                    "Cash Flow from Operations",
    "capex_monthly":          "Capital Expenditure",
    "cfi":                    "Cash Flow from Investing",
    "debt_drawdown":          "Debt Drawdown",
    "principal_repayment":    "Principal Repayment",
    "interest_paid":          "Interest Paid",
    "dividends_paid":         "Dividends Paid",
    "cff":                    "Cash Flow from Financing",
    "net_cash_flow":          "Net Cash Flow",
    "opening_cash":           "Opening Cash",
    "closing_cash":           "Closing Cash",
    # BS_001
    "fixed_assets_gross":       "Fixed Assets — Gross",
    "accumulated_depreciation": "Accumulated Depreciation",
    "fixed_assets_net":         "Fixed Assets — Net",
    "cash":                     "Cash & Equivalents",
    "total_assets":             "Total Assets",
    "debt_balance":             "Debt Balance",
    "contributed_equity":       "Contributed Equity",
    "retained_earnings":        "Retained Earnings",
    "total_equity":             "Total Equity",
    "total_liabilities_equity": "Total Liabilities & Equity",
    "imbalance":                "Balance Check (imbalance)",
    # IRR_001
    "cumulative_pfcf": "Cumulative PFCF",
    # PPA_CFD_001
    "cfd_flag":               "CfD Active Flag",
    "settlement_value":       "CfD Settlement",
    "strike_price_indexed":   "Strike Price (indexed)",
    # WORKING_CAPITAL_001
    "total_receivables":       "Total Receivables",
    "total_payables":          "Total Payables",
    "working_capital_balance": "Working Capital Balance",
    "working_capital_change":  "Working Capital Change",
    # SOURCES_USES_001
    "equity_monthly":          "Equity Disbursement",
    # VALUATION_001
    "ev":                      "Enterprise Value",
    # BREAKEVEN_001
    "breakeven_price_monthly": "Breakeven Price (DKK/MWh)",
    "project_irr":     "Project IRR",
    "equity_irr":      "Equity IRR",
    "project_npv":     "Project NPV",
    "equity_npv":      "Equity NPV",
    "payback_period_months": "Payback Period (months)",
}

FIELD_UNITS: dict[str, str] = {
    "net_production_MWh":             "MWh",
    "discharge_volume":               "MWh",
    "goo_volume":                     "MWh",
    "goo_lost_volume":                "MWh",
    "total_ppa_contracted_volume":    "MWh",
    "price_indexation_factor":        "x",
    "discharge_indexation_factor":    "x",
    "charge_indexation_factor":       "x",
    "dscr_annual":                    "x",
    "levered_beta":                   "x",
    "debt_to_equity_ratio":           "x",
    "wholesale_inflated":             "DKK/MWh",
    "capture_inflated":               "DKK/MWh",
    "discharge_price_inflated":       "DKK/MWh",
    "grid_charge_price_inflated":     "DKK/MWh",
    "pv_charge_price_inflated":       "DKK/MWh",
    "wacc":                           "%",
    "blended_cost_of_equity":         "%",
    "ppa_cost_of_equity":             "%",
    "merchant_cost_of_equity":        "%",
    "cost_of_debt_posttax":           "%",
    "ppa_erp":                        "%",
    "merchant_erp":                   "%",
    "project_irr":                    "%",
    "equity_irr":                     "%",
}

FIELD_UNITS_DEFAULT = "DKKk"


# ============================================================================
# FUNCTIONS
# ============================================================================

def col_letter(col_index: int) -> str:
    """Convert 1-based column index to Excel letter(s). 1→'A', 27→'AA'."""
    result = ""
    while col_index > 0:
        col_index, remainder = divmod(col_index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def period_col(period_index: int) -> int:
    """Convert 0-based period index to 1-based column index. Period 0 → col 3 (C)."""
    return period_index + 3


def get_cell(sheet: str, row: int, col: int) -> str:
    """Return cross-sheet cell reference, e.g. 'Revenue!C6'."""
    return f"{sheet}!{col_letter(col)}{row}"


def get_row(module_id: str, field_name: str) -> int:
    """Return the absolute row number for a given module output field."""
    sheet = MODULE_SHEET.get(module_id)
    if sheet is None:
        raise KeyError(f"Unknown module_id: {module_id!r}")
    key = (sheet, module_id, field_name)
    if key not in ROW_MAP:
        raise KeyError(f"No row assignment for {module_id}.{field_name!r}")
    return ROW_MAP[key]


def get_sheet(module_id: str) -> str:
    """Return the worksheet name for a given module."""
    if module_id not in MODULE_SHEET:
        raise KeyError(f"Unknown module_id: {module_id!r}")
    return MODULE_SHEET[module_id]


def get_label(field_name: str) -> str:
    """Return the display label for a field, defaulting to the field name."""
    return FIELD_LABELS.get(field_name, field_name)


def get_units(field_name: str) -> str:
    """Return the units string for a field, defaulting to 'DKKk'."""
    return FIELD_UNITS.get(field_name, FIELD_UNITS_DEFAULT)
