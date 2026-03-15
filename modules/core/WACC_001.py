# WACC_001.py
# Weighted Average Cost of Capital Module

MODULE_ID = "WACC_001"
VERSION = "1.0"
TIER = "both"
MARKETS = ["DK", "DE", "*"]
TECHNOLOGIES = ["PV", "BESS", "WIND", "*"]

from pydantic import BaseModel

class Inputs(BaseModel):
    equity_share: float
    cost_of_equity: float
    debt_share: float
    cost_of_debt: float
    tax_rate: float

class Outputs(BaseModel):
    wacc: float
    wacc_pre_tax: float

def calculate(inputs: Inputs) -> Outputs:
    """
    Calculate WACC and pre-tax WACC.
    """
    wacc_pre_tax = inputs.equity_share * inputs.cost_of_equity + inputs.debt_share * inputs.cost_of_debt
    wacc = wacc_pre_tax - inputs.debt_share * inputs.cost_of_debt * inputs.tax_rate
    return Outputs(wacc=wacc, wacc_pre_tax=wacc_pre_tax)

def get_excel_formulas(refs: dict) -> dict:
    """
    Return Excel formula strings referencing assumption cells.
    refs should be a dict like {'equity_share': 'A1', 'cost_of_equity': 'A2', etc.}
    """
    formulas = {
        'wacc_pre_tax': f"={refs['equity_share']}*{refs['cost_of_equity']}+{refs['debt_share']}*{refs['cost_of_debt']}",
        'wacc': f"={refs['equity_share']}*{refs['cost_of_equity']}+{refs['debt_share']}*{refs['cost_of_debt']}*(1-{refs['tax_rate']})"
    }
    return formulas

def get_default_assumptions(market: str) -> dict:
    """
    Return default assumptions for the given market.
    For DK: equity 40%, Ke 8%, Kd 4.5%, tax 22%
    """
    if market == "DK":
        return {
            "equity_share": 0.40,
            "cost_of_equity": 0.08,
            "debt_share": 0.60,
            "cost_of_debt": 0.045,
            "tax_rate": 0.22
        }
    else:
        # For other markets, use DK defaults for now
        return get_default_assumptions("DK")

def test():
    """
    Test with known values: 40% equity, 8% Ke, 60% debt, 4.5% Kd, 22% tax → WACC = 5.306%, Pre-tax WACC = 5.900%
    """
    inputs = Inputs(
        equity_share=0.40,
        cost_of_equity=0.08,
        debt_share=0.60,
        cost_of_debt=0.045,
        tax_rate=0.22
    )
    outputs = calculate(inputs)
    expected_wacc_pre_tax = 0.059  # 5.900%
    expected_wacc = 0.05306  # 5.306%
    assert abs(outputs.wacc_pre_tax - expected_wacc_pre_tax) < 1e-6
    assert abs(outputs.wacc - expected_wacc) < 1e-6
    print("Test passed!")

if __name__ == "__main__":
    test()