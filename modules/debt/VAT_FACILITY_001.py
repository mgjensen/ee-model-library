"""
MODULE_ID:    VAT_FACILITY_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Construction VAT financing facility.

During construction, the project pays VAT on capex. The tax authority
reimburses VAT after a delay. A VAT facility finances the gap:

    VAT paid[p]     = capex_monthly[p] × vat_rate
    VAT refund[p]   = VAT paid[p − reimbursement_delay_months]
    facility_balance = cumulative VAT paid − cumulative refunds

Interest accrues on the facility balance each month.
A commitment fee accrues on the *undrawn* portion of the facility limit
(= peak expected balance).

Source: EE_MODEL_BUILD_SPEC.md v2.0 §9.2
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int = Field(2025)
    start_month: int = Field(1, ge=1, le=12)

    # VAT parameters
    vat_rate: float = Field(0.25, ge=0, le=1.0, description="VAT rate (e.g. 0.25 = 25%)")
    reimbursement_delay_months: int = Field(
        1, ge=0, description="Months between VAT payment and tax authority refund"
    )
    margin: float = Field(0.0, ge=0, description="Annual interest margin on drawn balance")
    commitment_fee_rate: float = Field(
        0.0, ge=0, description="Annual commitment fee on undrawn facility"
    )

    # From CAPEX_001 — monthly capex spend (DKKk, positive = spend)
    capex_monthly: list[float] = Field(..., description="Monthly capex DKKk (length = periods)")

    @model_validator(mode="after")
    def _validate(self):
        if len(self.capex_monthly) != self.periods:
            raise ValueError(
                f"capex_monthly length {len(self.capex_monthly)} != periods {self.periods}"
            )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    vat_paid: list[float]              # DKKk — VAT on capex
    vat_refund: list[float]            # DKKk — refund from tax authority
    facility_balance: list[float]      # DKKk — outstanding balance (drawn)
    interest: list[float]              # DKKk — interest on drawn balance
    commitment_fee_paid: list[float]   # DKKk — fee on undrawn portion

    # Summary
    total_vat_paid: float
    total_vat_refund: float
    total_interest: float
    total_commitment_fee: float
    peak_balance: float                # DKKk — facility limit


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Monthly VAT facility schedule."""
    n = inputs.periods
    delay = inputs.reimbursement_delay_months
    monthly_margin = inputs.margin / 12.0
    monthly_commit = inputs.commitment_fee_rate / 12.0

    # VAT paid and refund
    vat_paid = [inputs.capex_monthly[p] * inputs.vat_rate for p in range(n)]
    vat_refund = [0.0] * n
    for p in range(n):
        src = p - delay
        if src >= 0:
            vat_refund[p] = vat_paid[src]

    # Facility balance = cumulative VAT paid − cumulative refunds
    balance = [0.0] * n
    cum_paid = 0.0
    cum_refund = 0.0
    for p in range(n):
        cum_paid += vat_paid[p]
        cum_refund += vat_refund[p]
        balance[p] = cum_paid - cum_refund

    # Peak balance = facility limit (for commitment fee calculation)
    peak = max(balance) if balance else 0.0

    # Interest on drawn balance
    interest = [balance[p] * monthly_margin for p in range(n)]

    # Commitment fee on undrawn portion
    commitment = [
        max(0.0, peak - balance[p]) * monthly_commit for p in range(n)
    ]

    return Outputs(
        vat_paid=vat_paid,
        vat_refund=vat_refund,
        facility_balance=balance,
        interest=interest,
        commitment_fee_paid=commitment,
        total_vat_paid=sum(vat_paid),
        total_vat_refund=sum(vat_refund),
        total_interest=sum(interest),
        total_commitment_fee=sum(commitment),
        peak_balance=peak,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "vat_paid": f"={r['capex']}*{r['vat_rate']}",
        "vat_refund": f"=OFFSET({r['vat_paid_col']},0,-{r['delay']})",
        "facility_balance": f"={r['cum_vat_paid']}-{r['cum_vat_refund']}",
        "interest": f"={r['facility_balance']}*{r['monthly_margin']}",
        "commitment_fee": f"=MAX(0,{r['peak']}-{r['facility_balance']})*{r['monthly_commit']}",
    }
