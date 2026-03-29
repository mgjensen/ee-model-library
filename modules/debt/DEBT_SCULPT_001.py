"""
MODULE_ID:    DEBT_SCULPT_001
VERSION:      1.0
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]
CREATED:      2026-03-17
MODIFIED:     2026-03-18

Sculpted senior debt for project finance.

DSCR-based debt sizing with per-revenue-stream blended targets,
semi-annual debt service payments, margin step-ups, and hedged/unhedged
interest rate blending.

Algorithm:
  1. Group months into semi-annual payment periods (e.g. Jun/Dec).
  2. For each SA period, compute blended DSCR = CFADS-weighted avg of
     per-stream targets.
  3. Max debt service per SA period = SA CFADS / blended DSCR.
  4. Sculpted principal = max DS − SA interest.
  5. Auto-size facility via bisection: largest balance where debt is
     fully repaid within tenor. Cap at leverage_cap_pct × total_capex.
  6. Interest = hedge_pct × (swap_rate + margin) + (1−hedge_pct) ×
     (reference_rate + margin), with margin stepping down over time.

Reference: EE_MODEL_BUILD_SPEC.md v2.0 §8, Holsted Hybrid vendor model
(sculpted debt, 15y, DSCR 1.2/1.6/1.6/2.1, 75% hedged at 3.068% swap).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Optional, Self
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# SUB-MODELS
# ============================================================================

class DSCRStream(BaseModel):
    """Per-revenue-stream DSCR target for sculpted repayment.

    Supports dual contracted/merchant DSCR targets. If merchant_dscr and
    merchant_start_period are set, target_dscr applies to contracted periods
    (before merchant_start_period) and merchant_dscr applies after.
    """
    name: str = Field(..., description="e.g. 'pv_contracted'")
    target_dscr: float = Field(..., gt=0, description="Contracted-period DSCR target")
    cfads: list[float] = Field(..., description="Monthly CFADS DKKk")
    merchant_dscr: Optional[float] = Field(
        None, gt=0, description="Merchant-period DSCR target (after PPA/tolling expires)"
    )
    merchant_start_period: Optional[int] = Field(
        None, ge=0, description="0-based period when merchant DSCR takes effect"
    )


class ConstraintScenario(BaseModel):
    """A single constraint scenario for multi-constraint sculpting.

    Each scenario has its own DSCRStreams (with their own CFADS and targets).
    The final amortisation is the element-wise minimum across all scenarios.
    """
    name: str = Field(..., description="e.g. 'P50', 'P99', 'breakeven'")
    dscr_streams: list[DSCRStream] = Field(
        ..., description="Per-stream DSCR targets and CFADS for this scenario"
    )


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

class Inputs(BaseModel):
    # Timeline
    periods: int = Field(..., gt=0)
    start_year: int
    start_month: int = Field(..., ge=1, le=12)

    # Revenue streams for DSCR blending
    dscr_streams: list[DSCRStream] = Field(
        ..., description="Per-stream DSCR targets and monthly CFADS"
    )

    # Debt terms
    tenor_months: int = Field(..., gt=0, description="Repayment tenor in months from first payment")
    grace_period_months: int = Field(
        0, ge=0, description="Months after construction_end before first payment"
    )
    payment_months: list[int] = Field(
        default_factory=lambda: [6, 12],
        description="Calendar months when debt service is due (e.g. [6, 12] = Jun/Dec)"
    )

    # Interest rate components
    swap_rate: float = Field(0.0, ge=0, description="Annual swap rate for hedged portion")
    hedge_pct: float = Field(0.75, ge=0, le=1.0, description="Fraction of debt hedged")
    reference_rate: float = Field(
        0.0, ge=0, description="Annual reference rate for unhedged portion (e.g. CIBOR fwd)"
    )
    margin_rates: list[float] = Field(
        ..., description="Annual margin for each step (e.g. [0.023, 0.020])"
    )
    margin_step_years: list[int] = Field(
        default_factory=lambda: [0],
        description="Year offsets from construction_end when each margin starts (e.g. [0, 10])"
    )

    # Sizing
    leverage_cap_pct: float = Field(0.80, gt=0, le=1.0)
    total_capex_DKKk: float = Field(..., gt=0)

    # Construction
    construction_end_period: int = Field(
        ..., ge=0, description="0-based period index of COD (first ops period)"
    )

    # Cash sweep
    cash_sweep_active: bool = Field(False, description="Enable cash sweep prepayment")
    cash_sweep_start_period: int = Field(
        0, ge=0, description="Period index when sweep begins (0 = from first repayment)"
    )
    cash_sweep_pct: float = Field(
        0.0, ge=0.0, le=1.0, description="Fraction of excess CFADS swept to prepay debt"
    )

    # DSCR covenant
    dscr_covenant: float = Field(1.10, gt=0)

    # Multi-constraint sculpting (optional — UC model pattern)
    constraint_scenarios: Optional[list[ConstraintScenario]] = Field(
        None, description="Multiple constraint scenarios. Final principal = min across all."
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        n = self.periods
        for i, s in enumerate(self.dscr_streams):
            if len(s.cfads) != n:
                raise ValueError(
                    f"dscr_streams[{i}].cfads length {len(s.cfads)} != periods {n}"
                )
        if len(self.margin_rates) != len(self.margin_step_years):
            raise ValueError("margin_rates and margin_step_years must have equal length")
        if not self.margin_rates:
            raise ValueError("margin_rates must have at least one entry")
        for m in self.payment_months:
            if m < 1 or m > 12:
                raise ValueError(f"payment_months must be 1-12, got {m}")
        if self.constraint_scenarios is not None:
            for ci, cs in enumerate(self.constraint_scenarios):
                for si, s in enumerate(cs.dscr_streams):
                    if len(s.cfads) != n:
                        raise ValueError(
                            f"constraint_scenarios[{ci}].dscr_streams[{si}].cfads "
                            f"length {len(s.cfads)} != periods {n}"
                        )
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]
    drawdown: list[float]
    interest_accrued: list[float]    # monthly interest accrual
    interest_paid: list[float]       # cash payment (payment months only)
    principal: list[float]           # cash payment (payment months only)
    cash_sweep: list[float]          # cash sweep prepayment per period DKKk
    closing_balance: list[float]
    debt_service: list[float]        # interest_paid + principal + cash_sweep

    blended_dscr_target: list[float]  # per-SA-period target mapped to months
    dscr_achieved: list[float]        # actual DSCR per SA period mapped to months

    # LLCR time-series
    llcr_series: list[float]          # per-period LLCR (NaN outside repayment)
    pv_cfads: list[float]             # PV of remaining CFADS from each period

    # DSCR lookback
    dscr_6m_lookback: list[float]     # 6-month backward-looking DSCR
    dscr_12m_lookback: list[float]    # 12-month backward-looking DSCR
    min_dscr_6m: float                # minimum 6m lookback during repayment
    min_dscr_12m: float               # minimum 12m lookback during repayment

    # Summary
    total_debt: float          # sized facility DKKk
    gearing: float             # total_debt / total_capex
    total_interest: float
    total_principal: float
    total_cash_sweep: float    # lifetime cash sweep total
    final_balance: float
    min_dscr: float
    avg_dscr: float
    fully_repaid: bool
    llcr: float                # Loan Life Coverage Ratio at COD
    min_llcr: float            # minimum LLCR during repayment
    covenant_breached: bool

    # Multi-constraint outputs (empty if single-pass)
    binding_constraint: list[str]     # constraint name that was binding at each period
    per_constraint_dscr: dict[str, list[float]]  # per-scenario DSCR series


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _sa_periods(
    periods: int, start_month: int, payment_months: list[int],
) -> list[list[int]]:
    """Group monthly period indices into semi-annual (or other) payment periods."""
    sa: list[list[int]] = []
    current: list[int] = []
    pm_set = set(payment_months)
    for p in range(periods):
        current.append(p)
        cal_month = ((start_month - 1 + p) % 12) + 1
        if cal_month in pm_set:
            sa.append(current)
            current = []
    if current:
        sa.append(current)
    return sa


def _margin_at_period(
    p: int,
    construction_end: int,
    margin_rates: list[float],
    margin_step_years: list[int],
) -> float:
    """Determine the applicable margin based on years since COD."""
    ops_months = max(0, p - construction_end)
    ops_years = ops_months / 12.0
    margin = margin_rates[0]
    for rate, step_year in zip(margin_rates, margin_step_years):
        if ops_years >= step_year:
            margin = rate
    return margin


def _monthly_rate(
    p: int,
    inputs: Inputs,
) -> float:
    """Blended all-in monthly interest rate at period p."""
    margin = _margin_at_period(
        p, inputs.construction_end_period,
        inputs.margin_rates, inputs.margin_step_years,
    )
    hedged = inputs.swap_rate + margin
    unhedged = inputs.reference_rate + margin
    annual = inputs.hedge_pct * hedged + (1.0 - inputs.hedge_pct) * unhedged
    return annual / 12.0


def _stream_target_at_period(stream: DSCRStream, p: int) -> float:
    """Return the applicable DSCR target for a stream at period p.

    Uses merchant_dscr if the period falls at or after merchant_start_period.
    """
    if (stream.merchant_dscr is not None
            and stream.merchant_start_period is not None
            and p >= stream.merchant_start_period):
        return stream.merchant_dscr
    return stream.target_dscr


def _blended_dscr_for_sa(
    streams: list[DSCRStream],
    sa_periods: list[int],
) -> float:
    """CFADS-weighted blended target DSCR for a semi-annual period.

    Uses the representative period (last in SA group) to pick contracted vs
    merchant target per stream, then blends by CFADS weight.
    """
    rep_period = sa_periods[-1]
    total_cfads = 0.0
    weighted = 0.0
    for s in streams:
        scf = sum(s.cfads[p] for p in sa_periods)
        target = _stream_target_at_period(s, rep_period)
        total_cfads += scf
        weighted += scf * target
    return weighted / total_cfads if total_cfads > 1e-9 else 999.0


def _total_cfads_monthly(streams: list[DSCRStream], n: int) -> list[float]:
    return [sum(s.cfads[p] for s in streams) for p in range(n)]


# ============================================================================
# SCULPTED SCHEDULE
# ============================================================================

def _run_sculpted(
    facility: float,
    inputs: Inputs,
    sa_groups: list[list[int]],
    rep_start: int,
    rep_end: int,
    total_cfads: list[float],
) -> tuple[list[float], list[float], list[float], list[float],
           list[float], list[float], list[float], list[float],
           list[float], float]:
    """
    Run one sculpted schedule for a given facility size.

    Drawdowns: spread evenly across construction periods.
    Returns: (opening, drawdown, interest_accrued, interest_paid,
              principal, closing, blended_target_monthly, dscr_monthly, ds, final_balance)
    """
    n = inputs.periods
    cep = inputs.construction_end_period

    # Drawdowns: evenly across construction
    drawdown = [0.0] * n
    if cep > 0:
        monthly_draw = facility / cep
        for p in range(cep):
            drawdown[p] = monthly_draw
    else:
        drawdown[0] = facility

    opening = [0.0] * n
    interest_accrued = [0.0] * n
    interest_paid = [0.0] * n
    principal = [0.0] * n
    closing = [0.0] * n
    blended_monthly = [float("nan")] * n
    dscr_monthly = [float("nan")] * n
    ds = [0.0] * n

    balance = 0.0

    for sa_months in sa_groups:
        # Identify if this SA period is in repayment window
        payment_period_idx = sa_months[-1]  # payment on last month of SA group
        is_repayment = rep_start <= payment_period_idx < rep_end

        # Compute blended DSCR target for this SA period
        blended = _blended_dscr_for_sa(inputs.dscr_streams, sa_months)
        for p in sa_months:
            blended_monthly[p] = blended

        # Forward pass: accrue interest monthly
        sa_interest = 0.0
        for p in sa_months:
            opening[p] = balance
            r = _monthly_rate(p, inputs)
            interest_accrued[p] = balance * r
            sa_interest += interest_accrued[p]
            balance += drawdown[p]
            closing[p] = balance

        if is_repayment and balance > 1e-9:
            sa_cfads = sum(total_cfads[p] for p in sa_months)
            max_ds = sa_cfads / blended if blended > 1e-9 else 0.0
            sa_principal = max(0.0, max_ds - sa_interest)
            sa_principal = min(sa_principal, balance)

            # Apply interest + principal payment on the last month of SA group
            pay_p = payment_period_idx
            interest_paid[pay_p] = sa_interest
            principal[pay_p] = sa_principal
            ds[pay_p] = sa_interest + sa_principal
            balance -= sa_principal
            closing[pay_p] = balance

            # Achieved DSCR
            actual_ds = sa_interest + sa_principal
            achieved = sa_cfads / actual_ds if actual_ds > 1e-9 else float("nan")
            for p in sa_months:
                dscr_monthly[p] = achieved
        elif is_repayment:
            # Zero balance — no DS needed
            pay_p = payment_period_idx
            interest_paid[pay_p] = sa_interest
            ds[pay_p] = sa_interest

    return (opening, drawdown, interest_accrued, interest_paid,
            principal, closing, blended_monthly, dscr_monthly, ds, balance)


def _forward_pass_with_principal(
    facility: float,
    inputs: Inputs,
    fixed_principal: list[float],
    sa_groups: list[list[int]],
    rep_start: int,
    total_cfads: list[float],
) -> tuple[list[float], list[float], list[float], list[float],
           list[float], list[float], list[float], list[float],
           list[float], float]:
    """Run a forward balance pass using a pre-determined principal schedule.

    This recomputes interest and balances consistently with the given principal.
    Returns the same tuple format as _run_sculpted().
    """
    n = inputs.periods
    cep = inputs.construction_end_period

    drawdown = [0.0] * n
    if cep > 0:
        monthly_draw = facility / cep
        for p in range(cep):
            drawdown[p] = monthly_draw
    else:
        drawdown[0] = facility

    opening = [0.0] * n
    interest_accrued = [0.0] * n
    interest_paid = [0.0] * n
    closing = [0.0] * n
    blended_monthly = [float("nan")] * n
    dscr_monthly = [float("nan")] * n
    ds = [0.0] * n

    balance = 0.0

    for sa_months in sa_groups:
        payment_period_idx = sa_months[-1]
        is_repayment = rep_start <= payment_period_idx

        blended = _blended_dscr_for_sa(inputs.dscr_streams, sa_months)
        for p in sa_months:
            blended_monthly[p] = blended

        sa_interest = 0.0
        for p in sa_months:
            opening[p] = balance
            r = _monthly_rate(p, inputs)
            interest_accrued[p] = balance * r
            sa_interest += interest_accrued[p]
            balance += drawdown[p]
            closing[p] = balance

        if is_repayment and balance > 1e-9:
            sa_principal = sum(fixed_principal[p] for p in sa_months)
            sa_principal = min(sa_principal, balance)
            pay_p = payment_period_idx
            interest_paid[pay_p] = sa_interest
            ds[pay_p] = sa_interest + sa_principal
            balance -= sa_principal
            closing[pay_p] = balance
            # Map the fixed principal back to the payment period
            # (sculpted puts all principal on last SA month)
            for p in sa_months:
                if p == pay_p:
                    pass  # principal already in fixed_principal[p]
                # Recalculate achieved DSCR
            sa_cfads = sum(total_cfads[p] for p in sa_months)
            actual_ds = sa_interest + sa_principal
            achieved = sa_cfads / actual_ds if actual_ds > 1e-9 else float("nan")
            for p in sa_months:
                dscr_monthly[p] = achieved
        elif is_repayment:
            pay_p = payment_period_idx
            interest_paid[pay_p] = sa_interest
            ds[pay_p] = sa_interest

    return (opening, drawdown, interest_accrued, interest_paid,
            fixed_principal, closing, blended_monthly, dscr_monthly, ds, balance)


def _run_sculpted_with_streams(
    facility: float,
    inputs: Inputs,
    streams: list[DSCRStream],
    sa_groups: list[list[int]],
    rep_start: int,
    rep_end: int,
) -> tuple[list[float], list[float], list[float]]:
    """Run sculpting with custom DSCRStreams (for multi-constraint).

    Returns (principal, dscr_monthly, total_cfads) — only what's needed
    to compare across constraint scenarios.
    """
    n = inputs.periods
    total_cfads = [sum(s.cfads[p] for s in streams) for p in range(n)]
    (_, _, _, _, principal, _, _, dscr_monthly, _, _) = _run_sculpted(
        facility,
        inputs.model_copy(update={"dscr_streams": streams}),
        sa_groups, rep_start, rep_end, total_cfads,
    )
    return principal, dscr_monthly, total_cfads


def _auto_size(
    inputs: Inputs,
    sa_groups: list[list[int]],
    rep_start: int,
    rep_end: int,
    total_cfads: list[float],
) -> float:
    """Find the largest facility where debt is fully repaid within tenor."""
    leverage_cap = inputs.leverage_cap_pct * inputs.total_capex_DKKk
    upper = leverage_cap

    # Check if leverage cap works
    *_, final = _run_sculpted(upper, inputs, sa_groups, rep_start, rep_end, total_cfads)
    if final < 0.01:
        return upper

    # Bisect
    lo, hi = 0.0, upper
    for _ in range(60):
        mid = (lo + hi) / 2.0
        *_, final = _run_sculpted(mid, inputs, sa_groups, rep_start, rep_end, total_cfads)
        if final < 0.01:
            lo = mid
        else:
            hi = mid
    return lo


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate(inputs: Inputs) -> Outputs:
    """Sculpted senior debt schedule with semi-annual payments."""
    n = inputs.periods
    cep = inputs.construction_end_period
    total_cfads = _total_cfads_monthly(inputs.dscr_streams, n)

    # Semi-annual payment period grouping
    sa_groups = _sa_periods(n, inputs.start_month, inputs.payment_months)

    # Repayment window
    rep_start = cep + inputs.grace_period_months
    rep_end = rep_start + inputs.tenor_months

    # Auto-size facility
    facility = _auto_size(inputs, sa_groups, rep_start, rep_end, total_cfads)

    # Multi-constraint sculpting: run parallel passes, take min principal
    binding = [""] * n
    per_constraint_dscr: dict[str, list[float]] = {}

    if inputs.constraint_scenarios is not None and len(inputs.constraint_scenarios) > 0:
        # Bisect to find largest facility where min-principal schedule fully repays
        leverage_cap = inputs.leverage_cap_pct * inputs.total_capex_DKKk

        def _trial(trial_facility: float) -> float:
            """Run all constraints at trial_facility, return final balance."""
            all_p: list[list[float]] = []
            for cs in inputs.constraint_scenarios:
                cs_cfads = [sum(s.cfads[p] for s in cs.dscr_streams) for p in range(n)]
                cs_inp = inputs.model_copy(update={"dscr_streams": cs.dscr_streams})
                (_, _, _, _, cs_princ, _, _, _, _, _) = _run_sculpted(
                    trial_facility, cs_inp, sa_groups, rep_start, rep_end, cs_cfads
                )
                all_p.append(cs_princ)
            min_p = [min(all_p[i][p] for i in range(len(all_p))) for p in range(n)]
            (_, _, _, _, _, _, _, _, _, fb) = _forward_pass_with_principal(
                trial_facility, inputs, min_p, sa_groups, rep_start, total_cfads
            )
            return fb

        # Bisect for facility size
        lo, hi = 0.0, min(facility, leverage_cap)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if _trial(mid) < 0.01:
                lo = mid
            else:
                hi = mid
        facility = lo

        # Final run with sized facility
        all_principals: list[list[float]] = []
        constraint_names: list[str] = []
        for cs in inputs.constraint_scenarios:
            cs_cfads = [sum(s.cfads[p] for s in cs.dscr_streams) for p in range(n)]
            cs_inp = inputs.model_copy(update={"dscr_streams": cs.dscr_streams})
            (_, _, _, _, cs_principal, _, _, cs_dscr, _, _) = _run_sculpted(
                facility, cs_inp, sa_groups, rep_start, rep_end, cs_cfads
            )
            all_principals.append(cs_principal)
            constraint_names.append(cs.name)
            per_constraint_dscr[cs.name] = cs_dscr

        # Element-wise minimum principal
        min_principal = [0.0] * n
        for p in range(n):
            vals = [(all_principals[i][p], constraint_names[i])
                    for i in range(len(all_principals))]
            min_val = min(v[0] for v in vals)
            min_principal[p] = min_val
            binding_names = [v[1] for v in vals if abs(v[0] - min_val) < 1e-6]
            binding[p] = binding_names[0] if binding_names else ""

        # Forward pass with blended min principal
        (opening, drawdown, interest_accrued, interest_paid,
         principal, closing, blended_monthly, dscr_monthly, ds, final_bal) = (
            _forward_pass_with_principal(
                facility, inputs, min_principal, sa_groups, rep_start, total_cfads
            )
        )
    else:
        # Single-pass (current behavior)
        (opening, drawdown, interest_accrued, interest_paid,
         principal, closing, blended_monthly, dscr_monthly, ds, final_bal) = (
            _run_sculpted(facility, inputs, sa_groups, rep_start, rep_end, total_cfads)
        )

    # Cash sweep — mandatory prepayment of excess CFADS
    sweep = [0.0] * n
    if inputs.cash_sweep_active and inputs.cash_sweep_pct > 0:
        sweep_start = max(inputs.cash_sweep_start_period, rep_start)
        for p in range(sweep_start, n):
            if closing[p] <= 1e-9:
                continue
            excess = total_cfads[p] - (interest_paid[p] + principal[p])
            sweep_amt = max(0.0, excess * inputs.cash_sweep_pct)
            sweep_amt = min(sweep_amt, closing[p])
            sweep[p] = sweep_amt
            closing[p] -= sweep_amt
        final_bal = closing[-1] if closing else 0.0

    # Debt service includes cash sweep
    ds = [interest_paid[p] + principal[p] + sweep[p] for p in range(n)]

    # DSCR stats (only from SA periods with principal > 0)
    dscr_vals = [v for v in dscr_monthly if not math.isnan(v) and v < 900]
    min_dscr = min(dscr_vals) if dscr_vals else float("nan")
    avg_dscr = sum(dscr_vals) / len(dscr_vals) if dscr_vals else float("nan")
    covenant_breached = any(d < inputs.dscr_covenant for d in dscr_vals)

    # LLCR at COD (scalar — backward compatible)
    cod_rate = _monthly_rate(cep, inputs)
    pv_cfads_cod = 0.0
    for p in range(cep, min(rep_end, n)):
        months_from_cod = p - cep
        pv_cfads_cod += total_cfads[p] / (1.0 + cod_rate) ** months_from_cod
    debt_at_cod = facility
    llcr_cod = pv_cfads_cod / debt_at_cod if debt_at_cod > 1e-9 else float("nan")

    # LLCR per-period time-series
    # Find maturity: last period with closing balance > 0
    maturity = max((p for p in range(n) if closing[p] > 0.01), default=0)
    llcr_series = [float("nan")] * n
    pv_cfads_arr = [0.0] * n
    for p in range(n):
        if opening[p] < 0.01 or p < cep:
            continue
        # Discount future CFADS back to period p
        pv = 0.0
        discount = 1.0
        for s in range(p, maturity + 1):
            pv += total_cfads[s] * discount
            discount *= 1.0 / (1.0 + _monthly_rate(s, inputs))
        pv_cfads_arr[p] = pv
        llcr_series[p] = pv / opening[p]

    llcr_vals = [v for v in llcr_series if not math.isnan(v)]
    min_llcr = min(llcr_vals) if llcr_vals else float("nan")

    # DSCR lookback windows — compute only at SA period boundaries (payment months)
    # to avoid cross-period misalignment that artificially depresses the metric
    pm_set = set(inputs.payment_months)
    dscr_6m = [float("nan")] * n
    dscr_12m = [float("nan")] * n
    for p in range(n):
        if opening[p] < 0.01 or p < cep:
            continue
        cal_month = ((inputs.start_month - 1 + p) % 12) + 1
        if cal_month not in pm_set:
            continue
        # 6-month lookback (aligned with SA period)
        start_6 = max(0, p - 5)
        sum_cfads_6 = sum(total_cfads[t] for t in range(start_6, p + 1))
        sum_ds_6 = sum(ds[t] for t in range(start_6, p + 1))
        dscr_6m[p] = sum_cfads_6 / sum_ds_6 if sum_ds_6 > 1e-9 else float("nan")
        # 12-month lookback
        start_12 = max(0, p - 11)
        sum_cfads_12 = sum(total_cfads[t] for t in range(start_12, p + 1))
        sum_ds_12 = sum(ds[t] for t in range(start_12, p + 1))
        dscr_12m[p] = sum_cfads_12 / sum_ds_12 if sum_ds_12 > 1e-9 else float("nan")

    dscr_6m_vals = [v for v in dscr_6m if not math.isnan(v)]
    dscr_12m_vals = [v for v in dscr_12m if not math.isnan(v)]
    min_dscr_6m = min(dscr_6m_vals) if dscr_6m_vals else float("nan")
    min_dscr_12m = min(dscr_12m_vals) if dscr_12m_vals else float("nan")

    gearing = facility / inputs.total_capex_DKKk if inputs.total_capex_DKKk > 0 else 0.0

    return Outputs(
        opening_balance=opening,
        drawdown=drawdown,
        interest_accrued=interest_accrued,
        interest_paid=interest_paid,
        principal=principal,
        cash_sweep=sweep,
        closing_balance=closing,
        debt_service=ds,
        blended_dscr_target=blended_monthly,
        dscr_achieved=dscr_monthly,
        llcr_series=llcr_series,
        pv_cfads=pv_cfads_arr,
        dscr_6m_lookback=dscr_6m,
        dscr_12m_lookback=dscr_12m,
        min_dscr_6m=min_dscr_6m,
        min_dscr_12m=min_dscr_12m,
        total_debt=facility,
        gearing=gearing,
        total_interest=sum(interest_paid),
        total_principal=sum(principal),
        total_cash_sweep=sum(sweep),
        final_balance=final_bal,
        min_dscr=min_dscr,
        avg_dscr=avg_dscr,
        fully_repaid=final_bal < 0.01,
        llcr=llcr_cod,
        min_llcr=min_llcr,
        covenant_breached=covenant_breached,
        binding_constraint=binding,
        per_constraint_dscr=per_constraint_dscr,
    )


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    r = refs
    return {
        "interest_accrued": f"={r['opening_bal']}*{r['monthly_rate']}",
        "sculpted_principal": (
            f"=MAX(0,SUMPRODUCT({r['cfads_sa']})/{r['blended_dscr']}"
            f"-{r['sa_interest']})"
        ),
        "closing_balance": f"={r['opening_bal']}+{r['drawdown']}-{r['principal']}",
        "cash_sweep": f"=MIN(MAX(0,({r['cfads_sa']}-{r['interest_paid']}-{r['principal']})*{r['sweep_pct']}),{r['closing_before_sweep']})",
        "debt_service": f"={r['interest_paid']}+{r['principal']}+{r['cash_sweep']}",
        "llcr": f"={r['pv_cfads']}/{r['opening_bal']}",
        "dscr_achieved": (
            f"=SUMPRODUCT({r['cfads_sa']})"
            f"/({r['interest_paid']}+{r['principal']})"
        ),
        "blended_rate": (
            f"={r['hedge_pct']}*({r['swap_rate']}+{r['margin']})"
            f"+(1-{r['hedge_pct']})*({r['ref_rate']}+{r['margin']})"
        ),
    }
