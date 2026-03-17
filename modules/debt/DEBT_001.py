"""
MODULE_ID:    DEBT_001
VERSION:      1.1
TIER:         detailed
MARKETS:      ["DK", "DE", "AU", "SE", "PL", "*"]
TECHNOLOGIES: ["PV", "BESS", "WIND", "*"]

Senior debt schedule for project finance.

Computes:
  - Monthly opening balance, drawdowns, interest, principal, closing balance
  - Debt service (interest + principal) per period
  - Annual DSCR mapped to monthly periods (when CFADS provided)
  - Covenant breach flag

Repayment types: annuity, straight_line, bullet, sculpted
  - sculpted: principal per year = CFADS / blended_DSCR − interest
    Accepts per-revenue-stream DSCR targets for blended weighting.
    Auto-sizes facility = min(DSCR-sized, leverage cap).

Interest calculated on opening balance each month (interest not capitalised).

Source: EE_MODEL_BUILD_SPEC.md v2.0 §8, §16 (Project Viuf: 450,000 DKKk, 25y, 5.0%)
"""

from __future__ import annotations

import math
from collections import OrderedDict
from pydantic import BaseModel, Field, model_validator
from typing import Optional


# ============================================================================
# SUB-MODELS
# ============================================================================

class MarginStep(BaseModel):
    """One step in a time-varying margin schedule."""
    margin: float = Field(..., description="Annual margin for this period")
    until_year: int = Field(
        ..., description="Calendar year (inclusive) at which this step ends. "
        "Last step applies through end of tenor."
    )


class DSCRStream(BaseModel):
    """Per-revenue-stream DSCR target for sculpted repayment."""
    name: str = Field(..., description="Stream identifier, e.g. 'pv_contracted'")
    target_dscr: float = Field(..., gt=0, description="Target DSCR for this stream")
    cfads: list[float] = Field(..., description="Monthly CFADS for this stream DKKk")


# ============================================================================
# INPUTS & OUTPUTS
# ============================================================================

_REPAYMENT_TYPES = ("annuity", "straight_line", "bullet", "sculpted")


class Inputs(BaseModel):
    # Facility terms
    facility: float = Field(..., gt=0, description="Total debt facility DKKk")
    all_in_rate: float = Field(..., gt=0, description="Annual all-in interest rate (margin + fees)")
    repayment_type: str = Field("annuity", description="annuity | straight_line | bullet | sculpted")
    tenor_months: int = Field(..., gt=0, description="Repayment period length in months")

    # Time-varying margin (optional — overrides all_in_rate when set)
    base_rate: Optional[float] = Field(
        None, description="Annual base/reference rate (e.g. CIBOR swap). "
        "Required when margin_schedule is set."
    )
    margin_schedule: Optional[list[MarginStep]] = Field(
        None, description="Margin step-ups: list of MarginStep(margin, until_year). "
        "Steps must be in chronological order. Last step applies through end of tenor."
    )

    # Drawdown schedule — monthly amounts, length = periods, must sum to <= facility
    drawdowns: list[float] = Field(..., description="Monthly drawdown amounts DKKk")

    # Repayment start — None infers from last non-zero drawdown + 1
    repayment_start_period: Optional[int] = Field(
        None, description="0-based period index when repayment begins (None = infer)"
    )

    # Timeline
    periods: int = Field(..., gt=0, description="Total monthly periods")
    start_year: int = Field(..., description="Calendar year of period 0")
    start_month: int = Field(..., ge=1, le=12, description="Calendar month (1-12) of period 0")

    # DSCR
    dscr_covenant: float = Field(1.10, description="Minimum DSCR covenant")
    cfads: Optional[list[float]] = Field(
        None, description="Monthly CFADS for DSCR calculation DKKk"
    )

    # Dual DSCR covenant — PPA vs merchant period
    dscr_covenant_ppa: Optional[float] = Field(
        None, gt=0, description="DSCR covenant during PPA period. None = uses dscr_covenant."
    )
    dscr_covenant_merchant: Optional[float] = Field(
        None, gt=0, description="DSCR covenant during merchant period. None = uses dscr_covenant."
    )
    ppa_start_period: Optional[int] = Field(None, ge=0, description="Period when PPA begins")
    ppa_end_period: Optional[int] = Field(None, ge=0, description="Period when PPA ends (inclusive)")

    # Cash sweep (mandatory prepayment from excess CFADS)
    cash_sweep_pct: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Fraction of CFADS (after debt service) swept as mandatory prepayment. "
        "0 = disabled. Vendor model typical: 0.20."
    )
    cash_sweep_start_period: Optional[int] = Field(
        None, ge=0,
        description="0-based period when cash sweep begins. None = same as repayment start."
    )

    # Sculpted-specific (ignored for other repayment types)
    sculpted_dscr_streams: Optional[list[DSCRStream]] = Field(
        None, description="Per-stream DSCR targets and CFADS for sculpted repayment"
    )
    leverage_cap_pct: Optional[float] = Field(
        None, gt=0, le=1.0, description="Max debt as fraction of total CAPEX"
    )
    total_capex_DKKk: Optional[float] = Field(
        None, gt=0, description="Total CAPEX DKKk (required when leverage_cap_pct set)"
    )

    @model_validator(mode="after")
    def _validate(self):
        n = self.periods
        if len(self.drawdowns) != n:
            raise ValueError(f"drawdowns length {len(self.drawdowns)} != periods {n}")
        if abs(sum(self.drawdowns) - self.facility) > 0.01:
            raise ValueError(
                f"drawdowns sum {sum(self.drawdowns):.1f} != facility {self.facility:.1f}"
            )
        if self.repayment_type not in _REPAYMENT_TYPES:
            raise ValueError(f"Unknown repayment_type: {self.repayment_type!r}")
        if self.cfads is not None and len(self.cfads) != n:
            raise ValueError(f"cfads length {len(self.cfads)} != periods {n}")
        # Margin schedule validation
        if self.margin_schedule is not None:
            if self.base_rate is None:
                raise ValueError("margin_schedule requires base_rate")
            if len(self.margin_schedule) == 0:
                raise ValueError("margin_schedule must not be empty")
            prev_year = -1
            for i, step in enumerate(self.margin_schedule):
                if step.until_year <= prev_year:
                    raise ValueError(
                        f"margin_schedule[{i}].until_year={step.until_year} "
                        f"must be > previous until_year={prev_year}"
                    )
                prev_year = step.until_year
        # Sculpted-specific validation
        if self.repayment_type == "sculpted":
            if not self.sculpted_dscr_streams:
                raise ValueError("sculpted repayment requires sculpted_dscr_streams")
            for i, s in enumerate(self.sculpted_dscr_streams):
                if len(s.cfads) != n:
                    raise ValueError(
                        f"sculpted_dscr_streams[{i}].cfads length "
                        f"{len(s.cfads)} != periods {n}"
                    )
            if self.leverage_cap_pct is not None and self.total_capex_DKKk is None:
                raise ValueError("leverage_cap_pct requires total_capex_DKKk")
        return self


class Outputs(BaseModel):
    # Monthly arrays (length = periods)
    opening_balance: list[float]    # DKKk — balance at start of period
    drawdown: list[float]           # DKKk — drawdown in period
    interest: list[float]           # DKKk — interest on opening balance
    principal: list[float]          # DKKk — scheduled principal repaid
    cash_sweep: list[float]         # DKKk — mandatory prepayment from excess CFADS
    closing_balance: list[float]    # DKKk — balance at end of period
    debt_service: list[float]       # DKKk — interest + principal + cash_sweep
    dscr_annual: list[float]        # Annual DSCR mapped to each period (nan if no CFADS)

    # Dual DSCR covenant
    dscr_covenant_applicable: list[float]  # Which covenant applies per period
    covenant_breached_ppa: bool            # Breach during PPA period
    covenant_breached_merchant: bool       # Breach during merchant period

    # Summary
    total_interest: float
    total_principal: float          # Scheduled principal (excl. sweep)
    total_cash_sweep: float         # Lifetime cash sweep prepayment
    final_balance: float            # Should be ~0 if fully repaid within model
    min_dscr: float                 # Minimum during repayment (nan if no CFADS)
    covenant_breached: bool
    fully_repaid: bool              # True if final_balance < 0.01 DKKk

    # Sculpted / auto-sizing
    sized_facility: float           # Auto-sized facility DKKk (= facility for non-sculpted)
    blended_dscr_annual: list[float]  # Blended target DSCR per period (nan for non-sculpted)


# ============================================================================
# HELPERS
# ============================================================================

def _year_groups(periods: int, start_year: int, start_month: int) -> OrderedDict:
    """Map period indices to calendar years."""
    groups: OrderedDict = OrderedDict()
    for p in range(periods):
        offset = start_month - 1 + p
        year = start_year + offset // 12
        groups.setdefault(year, []).append(p)
    return groups


def _infer_repayment_start(drawdowns: list[float]) -> int:
    """First period after the last non-zero drawdown (or 0 if no drawdowns)."""
    last = max((i for i, d in enumerate(drawdowns) if d > 1e-9), default=-1)
    return last + 1


def _annuity_payment(balance: float, monthly_rate: float, n: int) -> float:
    """Constant annuity payment for a given balance, rate, and n periods."""
    if n <= 0:
        return balance
    if monthly_rate < 1e-12:
        return balance / n
    return balance * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -n)


def _monthly_rates(inputs: Inputs) -> list[float]:
    """
    Build per-period monthly interest rate array.

    If margin_schedule is set, each period's rate = (base_rate + margin) / 12,
    where margin comes from the step whose until_year >= the period's calendar year.
    Otherwise, flat rate = all_in_rate / 12 for all periods.
    """
    n = inputs.periods
    if inputs.margin_schedule is None:
        return [inputs.all_in_rate / 12.0] * n

    rates = [0.0] * n
    for p in range(n):
        offset = inputs.start_month - 1 + p
        year = inputs.start_year + offset // 12
        # Find the applicable margin step
        margin = inputs.margin_schedule[-1].margin  # default: last step
        for step in inputs.margin_schedule:
            if year <= step.until_year:
                margin = step.margin
                break
        rates[p] = (inputs.base_rate + margin) / 12.0
    return rates


# ============================================================================
# SCULPTED HELPERS
# ============================================================================

def _blended_dscr_by_year(
    streams: list[DSCRStream],
    yg: OrderedDict,
) -> dict:
    """Compute CFADS-weighted blended target DSCR per calendar year."""
    blended = {}
    for year, yp in yg.items():
        weighted = 0.0
        total = 0.0
        for s in streams:
            scf = sum(s.cfads[p] for p in yp)
            total += scf
            weighted += scf * s.target_dscr
        blended[year] = weighted / total if total > 1e-9 else 999.0
    return blended


def _total_cfads_from_streams(streams: list[DSCRStream], n: int) -> list[float]:
    """Sum per-stream CFADS into total monthly CFADS."""
    return [sum(s.cfads[p] for s in streams) for p in range(n)]


def _run_sculpted_pass(
    scale: float,
    inputs: Inputs,
    rep_start: int,
    blended: dict,
    total_cfads: list[float],
    yg: OrderedDict,
):
    """
    Run one sculpted schedule at a given facility scale factor.

    Returns (opening, draws, interest, principal, closing, final_balance).

    Algorithm per year:
      1. Interest-only pass → estimate annual interest
      2. Max DS = year CFADS / blended DSCR
      3. Annual principal = max(0, max DS − estimated interest)
      4. Spread principal evenly across ops months
      5. Actual forward pass with drawdowns and principal
    """
    n = inputs.periods
    rates = _monthly_rates(inputs)
    draws = [d * scale for d in inputs.drawdowns]
    rep_end = rep_start + inputs.tenor_months

    # Pass 1: interest-only → estimate interest per period
    interest_est = [0.0] * n
    bal = 0.0
    for p in range(n):
        interest_est[p] = bal * rates[p]
        bal += draws[p]

    # Compute annual principal targets
    monthly_princ_by_year = {}
    for year, yp in yg.items():
        ops = [p for p in yp if rep_start <= p < rep_end]
        if not ops:
            monthly_princ_by_year[year] = 0.0
            continue
        ops_cfads = sum(total_cfads[p] for p in ops)
        ops_interest = sum(interest_est[p] for p in ops)
        target = blended[year]
        max_ds = ops_cfads / target if target > 1e-9 else 0.0
        annual_princ = max(0.0, max_ds - ops_interest)
        monthly_princ_by_year[year] = annual_princ / len(ops)

    # Period → year mapping
    p_to_year = {}
    for year, yp in yg.items():
        for p in yp:
            p_to_year[p] = year

    # Pass 2: actual schedule
    opening = [0.0] * n
    interest = [0.0] * n
    principal = [0.0] * n
    closing = [0.0] * n

    balance = 0.0
    for p in range(n):
        opening[p] = balance
        interest[p] = balance * rates[p]

        if rep_start <= p < rep_end and balance > 1e-9:
            principal[p] = min(monthly_princ_by_year[p_to_year[p]], balance)
        else:
            principal[p] = 0.0

        closing[p] = balance + draws[p] - principal[p]
        balance = closing[p]

    return opening, draws, interest, principal, closing, balance


def _auto_size_facility(
    inputs: Inputs,
    rep_start: int,
    blended: dict,
    total_cfads: list[float],
    yg: OrderedDict,
) -> float:
    """
    Find the largest facility scale factor where debt is fully repaid
    within the tenor.  Uses bisection (converges in ~50 iterations).

    Returns the scale factor (0.0–1.0) to apply to the nominal facility.
    """
    max_scale = 1.0
    if inputs.leverage_cap_pct is not None and inputs.total_capex_DKKk is not None:
        leverage_cap = inputs.leverage_cap_pct * inputs.total_capex_DKKk
        max_scale = min(max_scale, leverage_cap / inputs.facility)

    # Check if max scale already works
    _, _, _, _, _, fb = _run_sculpted_pass(
        max_scale, inputs, rep_start, blended, total_cfads, yg
    )
    if fb < 0.01:
        return max_scale

    # Bisect between 0 and max_scale
    lo, hi = 0.0, max_scale
    for _ in range(60):
        mid = (lo + hi) / 2.0
        _, _, _, _, _, fb = _run_sculpted_pass(
            mid, inputs, rep_start, blended, total_cfads, yg
        )
        if fb < 0.01:
            lo = mid
        else:
            hi = mid
    return lo


# ============================================================================
# CORE CALCULATION
# ============================================================================

def calculate_schedule(inputs: Inputs) -> Outputs:
    """
    Build the full monthly debt schedule.

    Balance mechanics:
      opening[p] = closing[p-1]
      interest[p] = opening[p] × monthly_rate
      closing[p]  = opening[p] + drawdown[p] − principal[p]
      (interest is paid from cash, never capitalised)
    """
    n = inputs.periods
    rates = _monthly_rates(inputs)
    r = inputs.all_in_rate / 12.0  # flat rate for annuity PMT formula
    yg = _year_groups(n, inputs.start_year, inputs.start_month)

    rep_start = inputs.repayment_start_period
    if rep_start is None:
        rep_start = _infer_repayment_start(inputs.drawdowns)

    # ------------------------------------------------------------------
    # SCULPTED path
    # ------------------------------------------------------------------
    if inputs.repayment_type == "sculpted":
        streams = inputs.sculpted_dscr_streams
        blended = _blended_dscr_by_year(streams, yg)
        total_cfads = _total_cfads_from_streams(streams, n)

        scale = _auto_size_facility(inputs, rep_start, blended, total_cfads, yg)
        sized_fac = inputs.facility * scale

        opening, draws, interest_arr, principal_arr, closing, final_bal = (
            _run_sculpted_pass(scale, inputs, rep_start, blended, total_cfads, yg)
        )

        debt_service = [interest_arr[p] + principal_arr[p] for p in range(n)]

        # Map blended DSCR to monthly periods
        p_to_year = {}
        for year, yp in yg.items():
            for p in yp:
                p_to_year[p] = year
        blended_monthly = [blended[p_to_year[p]] for p in range(n)]

        # DSCR — actual achieved (using total CFADS from streams)
        dscr_monthly = [float("nan")] * n
        min_dscr = float("nan")
        covenant_breached = False
        running_min = math.inf

        for year, yp in yg.values() if False else yg.items():
            year_ds = sum(debt_service[p] for p in yp)
            year_principal = sum(principal_arr[p] for p in yp)
            if year_ds < 1e-9 or year_principal < 1e-9:
                continue
            year_cfads = sum(total_cfads[p] for p in yp)
            dscr = year_cfads / year_ds
            for p in yp:
                dscr_monthly[p] = dscr
            if dscr < running_min:
                running_min = dscr
            if dscr < inputs.dscr_covenant:
                covenant_breached = True
        min_dscr = running_min if running_min < math.inf else float("nan")

        # Cash sweep — mandatory prepayment from excess CFADS
        sweep_arr = [0.0] * n
        if inputs.cash_sweep_pct > 0 and total_cfads:
            sweep_start = inputs.cash_sweep_start_period if inputs.cash_sweep_start_period is not None else rep_start
            for p in range(sweep_start, n):
                if closing[p] > 1e-9:
                    excess = max(0.0, total_cfads[p] - debt_service[p])
                    sweep = min(excess * inputs.cash_sweep_pct, closing[p])
                    sweep_arr[p] = sweep
                    closing[p] -= sweep
            # Recompute final balance after sweep
            final_bal = closing[-1] if closing else 0.0

        total_ds_with_sweep = [debt_service[p] + sweep_arr[p] for p in range(n)]

        return Outputs(
            opening_balance=opening,
            drawdown=draws,
            interest=interest_arr,
            principal=principal_arr,
            cash_sweep=sweep_arr,
            closing_balance=closing,
            debt_service=total_ds_with_sweep,
            dscr_annual=dscr_monthly,
            dscr_covenant_applicable=[inputs.dscr_covenant] * n,
            covenant_breached_ppa=False,
            covenant_breached_merchant=False,
            total_interest=sum(interest_arr),
            total_principal=sum(principal_arr),
            total_cash_sweep=sum(sweep_arr),
            final_balance=final_bal,
            min_dscr=min_dscr,
            covenant_breached=covenant_breached,
            fully_repaid=final_bal < 0.01,
            sized_facility=sized_fac,
            blended_dscr_annual=blended_monthly,
        )

    # ------------------------------------------------------------------
    # NON-SCULPTED path (annuity / straight_line / bullet)
    # ------------------------------------------------------------------
    balance_at_rep_start = sum(inputs.drawdowns[:rep_start])

    if inputs.repayment_type == "annuity":
        annuity_pmt = _annuity_payment(balance_at_rep_start, r, inputs.tenor_months)
        sl_principal = None
    elif inputs.repayment_type == "straight_line":
        sl_principal = balance_at_rep_start / inputs.tenor_months if inputs.tenor_months else 0.0
        annuity_pmt = None
    else:  # bullet
        annuity_pmt = None
        sl_principal = None

    opening_arr = [0.0] * n
    interest_arr = [0.0] * n
    principal_arr = [0.0] * n
    closing_arr = [0.0] * n

    balance = 0.0
    repayment_count = 0

    for p in range(n):
        opening_arr[p] = balance
        interest_arr[p] = balance * rates[p]

        if p < rep_start or balance < 1e-9:
            princ = 0.0
        else:
            repayment_count += 1
            if inputs.repayment_type == "annuity":
                princ = max(0.0, annuity_pmt - interest_arr[p])
            elif inputs.repayment_type == "straight_line":
                princ = sl_principal
            else:  # bullet
                princ = balance if repayment_count == inputs.tenor_months else 0.0
            princ = min(princ, balance)

        principal_arr[p] = princ
        closing_arr[p] = balance + inputs.drawdowns[p] - princ
        balance = closing_arr[p]

    debt_service = [interest_arr[p] + principal_arr[p] for p in range(n)]

    # DSCR — computed annually, mapped back to monthly periods
    dscr_monthly = [float("nan")] * n
    min_dscr = float("nan")
    covenant_breached = False
    covenant_breached_ppa = False
    covenant_breached_merchant = False

    # Determine applicable covenant per period
    dual = inputs.dscr_covenant_ppa is not None and inputs.ppa_start_period is not None and inputs.ppa_end_period is not None
    cov_ppa = inputs.dscr_covenant_ppa or inputs.dscr_covenant
    cov_merchant = inputs.dscr_covenant_merchant or inputs.dscr_covenant
    dscr_cov_applicable = [inputs.dscr_covenant] * n
    if dual:
        for p in range(n):
            if inputs.ppa_start_period <= p <= inputs.ppa_end_period:
                dscr_cov_applicable[p] = cov_ppa
            else:
                dscr_cov_applicable[p] = cov_merchant

    if inputs.cfads is not None:
        running_min = math.inf
        for year_periods in yg.values():
            year_ds = sum(debt_service[p] for p in year_periods)
            year_principal = sum(principal_arr[p] for p in year_periods)
            if year_ds < 1e-9 or year_principal < 1e-9:
                continue
            year_cfads = sum(inputs.cfads[p] for p in year_periods)
            dscr = year_cfads / year_ds
            for p in year_periods:
                dscr_monthly[p] = dscr
            if dscr < running_min:
                running_min = dscr

            # Dual covenant check: use stricter covenant if year straddles
            if dual:
                year_cov = max(dscr_cov_applicable[p] for p in year_periods)
                in_ppa = any(inputs.ppa_start_period <= p <= inputs.ppa_end_period for p in year_periods)
                in_merchant = any(p < inputs.ppa_start_period or p > inputs.ppa_end_period for p in year_periods)
                if dscr < year_cov:
                    covenant_breached = True
                if in_ppa and dscr < cov_ppa:
                    covenant_breached_ppa = True
                if in_merchant and dscr < cov_merchant:
                    covenant_breached_merchant = True
            else:
                if dscr < inputs.dscr_covenant:
                    covenant_breached = True
        min_dscr = running_min if running_min < math.inf else float("nan")

    final_bal = closing_arr[-1] if closing_arr else 0.0

    # Cash sweep — mandatory prepayment from excess CFADS (non-sculpted)
    sweep_arr = [0.0] * n
    cfads_for_sweep = inputs.cfads
    if inputs.cash_sweep_pct > 0 and cfads_for_sweep is not None:
        sweep_start = inputs.cash_sweep_start_period if inputs.cash_sweep_start_period is not None else rep_start
        for p in range(sweep_start, n):
            if closing_arr[p] > 1e-9:
                excess = max(0.0, cfads_for_sweep[p] - debt_service[p])
                sweep = min(excess * inputs.cash_sweep_pct, closing_arr[p])
                sweep_arr[p] = sweep
                closing_arr[p] -= sweep
        final_bal = closing_arr[-1] if closing_arr else 0.0

    total_ds_with_sweep = [debt_service[p] + sweep_arr[p] for p in range(n)]

    return Outputs(
        opening_balance=opening_arr,
        drawdown=list(inputs.drawdowns),
        interest=interest_arr,
        principal=principal_arr,
        cash_sweep=sweep_arr,
        closing_balance=closing_arr,
        debt_service=total_ds_with_sweep,
        dscr_annual=dscr_monthly,
        dscr_covenant_applicable=dscr_cov_applicable,
        covenant_breached_ppa=covenant_breached_ppa,
        covenant_breached_merchant=covenant_breached_merchant,
        total_interest=sum(interest_arr),
        total_principal=sum(principal_arr),
        total_cash_sweep=sum(sweep_arr),
        final_balance=final_bal,
        min_dscr=min_dscr,
        covenant_breached=covenant_breached,
        fully_repaid=final_bal < 0.01,
        sized_facility=inputs.facility,
        blended_dscr_annual=[float("nan")] * n,
    )


# Alias for consistency with other modules
def calculate(inputs: Inputs) -> Outputs:
    return calculate_schedule(inputs)


# ============================================================================
# EXCEL FORMULA STRINGS
# ============================================================================

def get_excel_formulas(refs: dict) -> dict:
    """
    Returns dynamic Excel formula strings for key debt schedule rows.
    refs keys: opening_bal, drawdown, monthly_rate, annuity_pmt,
               principal, interest, debt_service, cfads
    """
    r = refs
    return {
        "opening_balance":  f"={r['closing_prev']}",
        "interest":         f"={r['opening_bal']}*{r['monthly_rate']}",
        "annuity_principal": f"=MAX(0,{r['annuity_pmt']}-{r['interest']})",
        "sl_principal":     f"={r['sl_principal_fixed']}",
        "sculpted_principal": (
            f"=MAX(0,(SUMIFS({r['cfads_range']},{r['year_range']},{r['year_ref']})"
            f"/{r['blended_dscr']}"
            f"-SUMIFS({r['interest_range']},{r['year_range']},{r['year_ref']}))"
            f"/COUNTIFS({r['year_range']},{r['year_ref']},{r['ops_flag_range']},1))"
        ),
        "closing_balance":  f"={r['opening_bal']}+{r['drawdown']}-{r['principal']}",
        "debt_service":     f"={r['interest']}+{r['principal']}",
        "dscr_annual":      f"=SUMIFS({r['cfads_range']},{r['year_range']},{r['year_ref']})"
                            f"/SUMIFS({r['ds_range']},{r['year_range']},{r['year_ref']})",
        "annuity_payment":  (
            f"={r['balance']}*{r['monthly_rate']}"
            f"/(1-(1+{r['monthly_rate']})^(-{r['tenor_months']}))"
        ),
    }
