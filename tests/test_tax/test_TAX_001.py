"""Tests for TAX_001 — Danish corporate tax module."""

import pytest
from modules.tax.TAX_001 import (
    Inputs,
    Outputs,
    N_BUCKETS,
    BUCKET_NAMES,
    calculate,
    calculate_declining_balance,
    calculate_ebitda_rule,
    calculate_tax_charge,
    get_excel_formulas,
    _year_groups,
    _payment_periods,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inputs(
    periods=24,
    start_year=2025,
    start_month=1,
    ebitda_annual=10_000.0,   # DKKk/year → /12 per month
    interest_annual=5_000.0,
    capex_year1=100_000.0,    # in bucket 0, month 0 only
    opening_bal=0.0,
):
    """Build a simple 2-year Inputs with flat monthly EBITDA and interest."""
    monthly_ebitda = [ebitda_annual / 12] * periods
    monthly_interest = [interest_annual / 12] * periods
    capex = [[0.0] * periods for _ in range(4)]
    capex[0][0] = capex_year1
    return Inputs(
        periods=periods,
        start_year=start_year,
        start_month=start_month,
        ebitda=monthly_ebitda,
        total_interest=monthly_interest,
        capex_by_bucket=capex,
        opening_balances=[opening_bal, 0.0, 0.0, 0.0],
    )


# ---------------------------------------------------------------------------
# Unit: _year_groups
# ---------------------------------------------------------------------------

def test_year_groups_calendar_year_split():
    yg = _year_groups(24, 2025, 1)
    assert list(yg.keys()) == [2025, 2026]
    assert len(yg[2025]) == 12
    assert len(yg[2026]) == 12


def test_year_groups_mid_year_start():
    """Start in July 2025: first year has 6 months, next has 12, etc."""
    yg = _year_groups(18, 2025, 7)
    assert list(yg.keys()) == [2025, 2026]
    assert len(yg[2025]) == 6
    assert len(yg[2026]) == 12


def test_year_groups_single_year():
    yg = _year_groups(12, 2030, 1)
    assert list(yg.keys()) == [2030]
    assert len(yg[2030]) == 12


# ---------------------------------------------------------------------------
# Unit: calculate_declining_balance
# ---------------------------------------------------------------------------

def _uniform_rates(rate, n=N_BUCKETS):
    return [rate] * n


def test_declining_balance_zero_opening_no_capex():
    yg = _year_groups(24, 2025, 1)
    annual_dep, monthly_dep = calculate_declining_balance(
        [0.0] * N_BUCKETS, [[0.0] * 24 for _ in range(N_BUCKETS)],
        _uniform_rates(0.15), yg, 24
    )
    assert all(d == 0.0 for d in annual_dep)
    assert all(d == 0.0 for d in monthly_dep)


def test_declining_balance_single_bucket_year1():
    """100,000 DKKk opening in bucket 0, 15% rate → 15,000 year-1 dep, 85,000 closing."""
    yg = _year_groups(24, 2025, 1)
    opening = [100_000.0] + [0.0] * (N_BUCKETS - 1)
    annual_dep, monthly_dep = calculate_declining_balance(
        opening,
        [[0.0] * 24 for _ in range(N_BUCKETS)],
        _uniform_rates(0.15), yg, 24
    )
    assert annual_dep[0] == pytest.approx(15_000.0)
    assert annual_dep[1] == pytest.approx(85_000.0 * 0.15)


def test_declining_balance_monthly_sum_equals_annual():
    yg = _year_groups(24, 2025, 1)
    opening = [100_000.0] + [0.0] * (N_BUCKETS - 1)
    annual_dep, monthly_dep = calculate_declining_balance(
        opening,
        [[0.0] * 24 for _ in range(N_BUCKETS)],
        _uniform_rates(0.15), yg, 24
    )
    assert sum(monthly_dep[:12]) == pytest.approx(annual_dep[0])
    assert sum(monthly_dep[12:]) == pytest.approx(annual_dep[1])


def test_declining_balance_capex_increases_basis():
    """Capex of 50,000 in year 1 month 0, opening 0 → dep = 50,000 × 0.15."""
    yg = _year_groups(24, 2025, 1)
    capex = [[0.0] * 24 for _ in range(N_BUCKETS)]
    capex[0][0] = 50_000.0
    annual_dep, _ = calculate_declining_balance(
        [0.0] * N_BUCKETS, capex, _uniform_rates(0.15), yg, 24
    )
    assert annual_dep[0] == pytest.approx(7_500.0)  # 50,000 × 0.15


def test_declining_balance_four_buckets_uniform_rate():
    """Verify first 4 buckets contribute with uniform rate."""
    yg = _year_groups(12, 2025, 1)
    opening = [10_000.0, 20_000.0, 5_000.0, 1_000.0] + [0.0] * (N_BUCKETS - 4)
    annual_dep, _ = calculate_declining_balance(
        opening, [[0.0] * 12 for _ in range(N_BUCKETS)],
        _uniform_rates(0.15), yg, 12
    )
    expected = sum(o * 0.15 for o in opening)
    assert annual_dep[0] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Unit: calculate_ebitda_rule
# ---------------------------------------------------------------------------

def test_ebitda_rule_interest_below_unrestricted():
    """Interest < unrestricted → fully deductible, no non-deductible."""
    ded, non_ded = calculate_ebitda_rule(
        annual_ebitda=[50_000.0],
        annual_interest=[5_000.0],
        unrestricted_DKKk=21_300.0,
        ebitda_pct=0.30,
        inflation_rate=0.02,
        carryforward_years=5,
    )
    assert non_ded[0] == pytest.approx(0.0)
    assert ded[0] == pytest.approx(5_000.0)


def test_ebitda_rule_interest_above_allowed():
    """Interest > unrestricted + EBITDA restriction → non-deductible amount."""
    # EBITDA=0 so no EBITDA restriction; unrestricted=5,000; interest=8,000
    ded, non_ded = calculate_ebitda_rule(
        annual_ebitda=[0.0],
        annual_interest=[8_000.0],
        unrestricted_DKKk=5_000.0,
        ebitda_pct=0.30,
        inflation_rate=0.0,
        carryforward_years=5,
    )
    assert non_ded[0] == pytest.approx(3_000.0)
    assert ded[0] == pytest.approx(5_000.0)


def test_ebitda_rule_ebitda_restriction_helps():
    """High EBITDA raises allowed deduction, reducing non-deductible."""
    # unrestricted=5,000; EBITDA=100,000 × 30% = 30,000; allowed=35,000; interest=30,000
    ded, non_ded = calculate_ebitda_rule(
        annual_ebitda=[100_000.0],
        annual_interest=[30_000.0],
        unrestricted_DKKk=5_000.0,
        ebitda_pct=0.30,
        inflation_rate=0.0,
        carryforward_years=5,
    )
    assert non_ded[0] == pytest.approx(0.0)
    assert ded[0] == pytest.approx(30_000.0)


def test_ebitda_rule_carryforward_used_next_year():
    """Non-deductible in year 0 is drawn back in year 1 when surplus exists."""
    # Year 0: unrestricted=5k, interest=8k → non-ded=3k, pool={3k, exp=y5}
    # Year 1: unrestricted=5k, interest=1k → surplus=4k → draws 3k from pool
    ded, non_ded = calculate_ebitda_rule(
        annual_ebitda=[0.0, 0.0],
        annual_interest=[8_000.0, 1_000.0],
        unrestricted_DKKk=5_000.0,
        ebitda_pct=0.30,
        inflation_rate=0.0,
        carryforward_years=5,
    )
    assert non_ded[0] == pytest.approx(3_000.0)
    assert non_ded[1] == pytest.approx(0.0)
    # Year 1 deductible = 1,000 (actual) + 3,000 (from pool) = 4,000
    assert ded[1] == pytest.approx(4_000.0)


def test_ebitda_rule_carryforward_expires():
    """Non-deductible expires after carryforward_years; not used after expiry."""
    n = 7
    # Year 0: non-ded=3k, pool expires year 2 (carryforward_years=2)
    # Years 1,2: interest=7k, no surplus → pool not drawn
    # Year 3: interest=7k again, pool expired → still non-deductible
    ebitda = [0.0] * n
    interest = [8_000.0] + [7_000.0] * (n - 1)
    ded, non_ded = calculate_ebitda_rule(
        annual_ebitda=ebitda,
        annual_interest=interest,
        unrestricted_DKKk=5_000.0,
        ebitda_pct=0.30,
        inflation_rate=0.0,
        carryforward_years=2,
    )
    # Year 0 non-ded = 3k (pool expires at y=0+2=2, meaning NOT usable at y=2)
    assert non_ded[0] == pytest.approx(3_000.0)
    # Year 1 interest == allowed (7k > 5k → non-ded 2k), no surplus to draw
    # Years 3+: pool from year 0 is expired
    assert ded[3] == pytest.approx(5_000.0)  # only unrestricted


# ---------------------------------------------------------------------------
# Unit: calculate_tax_charge
# ---------------------------------------------------------------------------

def test_tax_charge_profitable_from_start():
    """No losses → tax = taxable_income × rate."""
    taxable, charge = calculate_tax_charge(
        annual_ebitda=[100_000.0],
        annual_depreciation=[20_000.0],
        annual_deductible_interest=[5_000.0],
        tax_rate=0.22,
        inflation_rate=0.0,
        loss_full_limit_DKKk=999_999.0,
        loss_partial_pct=0.60,
    )
    assert taxable[0] == pytest.approx(75_000.0)
    assert charge[0] == pytest.approx(75_000.0 * 0.22)


def test_tax_charge_loss_year_no_tax():
    taxable, charge = calculate_tax_charge(
        annual_ebitda=[-5_000.0],
        annual_depreciation=[0.0],
        annual_deductible_interest=[0.0],
        tax_rate=0.22,
        inflation_rate=0.0,
        loss_full_limit_DKKk=20_829.0,
        loss_partial_pct=0.60,
    )
    assert charge[0] == pytest.approx(0.0)
    assert taxable[0] == pytest.approx(-5_000.0)


def test_tax_charge_loss_carryforward_full_relief():
    """Loss in year 0, profit in year 1 fully relieved (within full limit)."""
    # Year 0: gross = −10,000 → pool = 10,000
    # Year 1: gross = 8,000 → full relief = min(10,000, 20,829) = 8,000 → net = 0
    taxable, charge = calculate_tax_charge(
        annual_ebitda=[-10_000.0, 18_000.0],
        annual_depreciation=[0.0, 0.0],
        annual_deductible_interest=[0.0, 10_000.0],
        tax_rate=0.22,
        inflation_rate=0.0,
        loss_full_limit_DKKk=20_829.0,
        loss_partial_pct=0.60,
    )
    assert taxable[0] < 0
    assert taxable[1] == pytest.approx(0.0)
    assert charge[1] == pytest.approx(0.0)


def test_tax_charge_partial_relief():
    """Income above full limit → 60% partial relief on remainder."""
    # loss_pool = 5,000; gross = 30,000; full_limit = 20,829
    # full_relief = 5,000 (pool < limit); remaining = 25,000
    # partial = min(0, 25,000 × 0.60) = 0 (pool exhausted by full relief)
    # Actually: full_relief = min(pool=5000, limit=20829) = 5000
    # remaining_income = 30,000 - 5,000 = 25,000
    # partial = min(pool-full_relief=0, 25,000×0.60) = 0
    # net = 25,000 → charge = 25,000 × 0.22
    taxable, charge = calculate_tax_charge(
        annual_ebitda=[-5_000.0, 30_000.0],
        annual_depreciation=[0.0, 0.0],
        annual_deductible_interest=[0.0, 0.0],
        tax_rate=0.22,
        inflation_rate=0.0,
        loss_full_limit_DKKk=20_829.0,
        loss_partial_pct=0.60,
    )
    assert taxable[1] == pytest.approx(25_000.0)
    assert charge[1] == pytest.approx(25_000.0 * 0.22)


def test_tax_charge_large_loss_partial_applies():
    """Pool > full_limit → partial relief applies to the remainder."""
    # Pool = 100,000; gross = 50,000
    # full_relief = min(100k, 20,829) = 20,829
    # remaining = 50,000 - 20,829 = 29,171
    # partial = min(pool - 20,829 = 79,171, 29,171 × 0.60) = 17,502.6
    # total_relief = 20,829 + 17,502.6 = 38,331.6
    # net = 50,000 - 38,331.6 = 11,668.4
    taxable, charge = calculate_tax_charge(
        annual_ebitda=[-100_000.0, 50_000.0],
        annual_depreciation=[0.0, 0.0],
        annual_deductible_interest=[0.0, 0.0],
        tax_rate=0.22,
        inflation_rate=0.0,
        loss_full_limit_DKKk=20_829.0,
        loss_partial_pct=0.60,
    )
    expected_net = 50_000.0 - 20_829.0 - (50_000.0 - 20_829.0) * 0.60
    assert taxable[1] == pytest.approx(expected_net, rel=1e-6)
    assert charge[1] == pytest.approx(expected_net * 0.22, rel=1e-6)


# ---------------------------------------------------------------------------
# Unit: payment timing
# ---------------------------------------------------------------------------

def test_payment_periods_correct_months():
    """
    2-year model starting Jan 2025:
    Year 2025 → current payment in Nov 2025 (period 10), prior in Mar 2026 (period 14)
    Year 2026 → current payment in Nov 2026 (period 22), prior in Mar 2027 (None — out of range)
    """
    yg = _year_groups(24, 2025, 1)
    pm = _payment_periods(yg, 2025, 1, 24, payment_month_prior=3, payment_month_current=11)
    # 2025 current = Nov 2025 = period 10 (0-indexed)
    assert pm[2025]["current"] == 10
    # 2025 prior = Mar 2026 = period 14
    assert pm[2025]["prior"] == 14
    # 2026 current = Nov 2026 = period 22
    assert pm[2026]["current"] == 22
    # 2026 prior = Mar 2027 = period 26 → outside 24 periods → None
    assert pm[2026]["prior"] is None


def test_payment_periods_mid_year_start():
    """Model starts Jul 2025 (12 periods = 6 months 2025 + 6 months 2026)."""
    yg = _year_groups(12, 2025, 7)
    pm = _payment_periods(yg, 2025, 7, 12, payment_month_prior=3, payment_month_current=11)
    # Nov 2025 = period 4 (Jul=0, Aug=1, Sep=2, Oct=3, Nov=4)
    assert pm[2025]["current"] == 4
    # Mar 2026 = period 8 (Jul=0 ... Dec=5, Jan=6, Feb=7, Mar=8)
    assert pm[2025]["prior"] == 8


# ---------------------------------------------------------------------------
# Integration: calculate()
# ---------------------------------------------------------------------------

def test_calculate_returns_outputs_instance():
    assert isinstance(calculate(_make_inputs()), Outputs)


def test_calculate_monthly_arrays_correct_length():
    out = calculate(_make_inputs(periods=36))
    assert len(out.tax_paid) == 36
    assert len(out.tax_depreciation) == 36
    assert len(out.tax_charge_accrued) == 36


def test_calculate_no_capex_no_opening_no_depreciation():
    inp = _make_inputs(capex_year1=0.0, opening_bal=0.0)
    out = calculate(inp)
    assert out.total_tax_depreciation == pytest.approx(0.0)


def test_calculate_monthly_dep_sums_to_total():
    out = calculate(_make_inputs(capex_year1=200_000.0))
    assert sum(out.tax_depreciation) == pytest.approx(out.total_tax_depreciation, rel=1e-9)


def test_calculate_tax_paid_sums_to_total():
    out = calculate(_make_inputs())
    assert sum(out.tax_paid) == pytest.approx(out.total_tax_paid, rel=1e-9)


def test_calculate_tax_paid_nonnegative():
    out = calculate(_make_inputs())
    assert all(v >= -1e-9 for v in out.tax_paid)


def test_calculate_zero_ebitda_no_tax():
    """No EBITDA → no taxable income → no tax paid."""
    inp = _make_inputs(ebitda_annual=0.0, interest_annual=0.0, capex_year1=0.0, opening_bal=0.0)
    out = calculate(inp)
    assert out.total_tax_paid == pytest.approx(0.0)


def test_calculate_zero_interest_full_deductible():
    """Zero interest → no EBITDA rule restriction, tax purely on EBITDA - depreciation."""
    out = calculate(_make_inputs(ebitda_annual=50_000.0, interest_annual=0.0,
                                  capex_year1=0.0, opening_bal=0.0))
    # Taxable income = EBITDA - 0 dep - 0 interest; tax = 22% per year
    expected_annual_tax = 50_000.0 * 0.22
    # Two years of tax accrued
    assert sum(out.annual_tax_charge) == pytest.approx(2 * expected_annual_tax, rel=1e-4)


def test_calculate_loss_years_suppress_tax():
    """Heavily loss-making project → zero total tax paid."""
    inp = _make_inputs(ebitda_annual=-100_000.0, interest_annual=0.0,
                        capex_year1=0.0, opening_bal=0.0)
    out = calculate(inp)
    assert out.total_tax_paid == pytest.approx(0.0)


def test_calculate_payment_months_only():
    """Tax cash payments should only appear in months 3 (Mar) and 11 (Nov)."""
    out = calculate(_make_inputs(periods=36, start_year=2025, start_month=1))
    for p, val in enumerate(out.tax_paid):
        if abs(val) > 1e-9:
            # Determine calendar month of this period
            month = ((1 - 1 + p) % 12) + 1
            assert month in (3, 11), f"Unexpected payment in month {month} (period {p})"


def test_calculate_high_interest_triggers_non_deductible():
    """Interest >> EBITDA + unrestricted → non-zero non-deductible interest."""
    # DK unrestricted = 21,300 DKKk; set annual interest = 200,000 DKKk, EBITDA = 0
    inp = _make_inputs(ebitda_annual=0.0, interest_annual=200_000.0,
                        capex_year1=0.0, opening_bal=0.0)
    out = calculate(inp)
    assert out.total_non_deductible_interest > 0


def test_calculate_interest_within_unrestricted_no_restriction():
    """Interest < DK unrestricted (21,300) → no non-deductible interest."""
    inp = _make_inputs(ebitda_annual=0.0, interest_annual=10_000.0,
                        capex_year1=0.0, opening_bal=0.0)
    out = calculate(inp)
    assert out.total_non_deductible_interest == pytest.approx(0.0)


def test_calculate_invalid_country():
    capex = [[0.0] * 12 for _ in range(4)]
    with pytest.raises(FileNotFoundError):
        calculate(Inputs(
            country="XX", periods=12, start_year=2025, start_month=1,
            ebitda=[0.0] * 12, total_interest=[0.0] * 12,
            capex_by_bucket=capex, opening_balances=[0.0, 0.0, 0.0, 0.0],
        ))


# ---------------------------------------------------------------------------
# Backward compatibility: 4-bucket inputs padded to 7
# ---------------------------------------------------------------------------

def test_four_buckets_accepted_and_padded():
    """4-bucket capex_by_bucket is accepted and padded to 7."""
    inp = _make_inputs()  # uses 4 buckets
    assert len(inp.capex_by_bucket) == N_BUCKETS
    assert len(inp.opening_balances) == N_BUCKETS


def test_four_bucket_opening_balances_padded():
    inp = Inputs(
        periods=12, start_year=2025, start_month=1,
        ebitda=[0.0] * 12, total_interest=[0.0] * 12,
        capex_by_bucket=[[0.0] * 12 for _ in range(4)],
        opening_balances=[100.0, 200.0, 300.0, 400.0],
    )
    assert len(inp.opening_balances) == N_BUCKETS
    assert inp.opening_balances[:4] == [100.0, 200.0, 300.0, 400.0]
    assert inp.opening_balances[4:] == [0.0, 0.0, 0.0]


def test_seven_buckets_accepted():
    """Full 7-bucket inputs are accepted without padding."""
    n = 12
    inp = Inputs(
        periods=n, start_year=2025, start_month=1,
        ebitda=[1000.0] * n, total_interest=[0.0] * n,
        capex_by_bucket=[[0.0] * n for _ in range(N_BUCKETS)],
        opening_balances=[0.0] * N_BUCKETS,
    )
    assert len(inp.capex_by_bucket) == N_BUCKETS


def test_invalid_bucket_count_rejected():
    """5 or 6 buckets should be rejected."""
    with pytest.raises(ValueError, match="4 or 7"):
        Inputs(
            periods=12, start_year=2025, start_month=1,
            ebitda=[0.0] * 12, total_interest=[0.0] * 12,
            capex_by_bucket=[[0.0] * 12 for _ in range(5)],
        )


# ---------------------------------------------------------------------------
# 7-bucket: per-bucket depreciation rates
# ---------------------------------------------------------------------------

def test_seven_buckets_different_rates():
    """Each bucket uses its own rate from depreciation_rates in DK.json."""
    yg = _year_groups(12, 2025, 1)
    opening = [100_000.0, 50_000.0, 30_000.0, 20_000.0, 10_000.0, 5_000.0, 15_000.0]
    rates = [0.25, 0.25, 0.25, 0.25, 0.04, 0.25, 0.25]
    annual_dep, _ = calculate_declining_balance(
        opening, [[0.0] * 12 for _ in range(N_BUCKETS)],
        rates, yg, 12
    )
    expected = sum(o * r for o, r in zip(opening, rates))
    assert annual_dep[0] == pytest.approx(expected)


def test_land_bucket_lower_rate():
    """Bucket 4 (land) at 4% vs others at 25% — land depreciates much slower."""
    yg = _year_groups(24, 2025, 1)
    opening = [0.0] * N_BUCKETS
    opening[0] = 100_000.0  # PV at 25%
    opening[4] = 100_000.0  # Land at 4%
    rates = [0.25, 0.25, 0.25, 0.25, 0.04, 0.25, 0.25]
    annual_dep, _ = calculate_declining_balance(
        opening, [[0.0] * 24 for _ in range(N_BUCKETS)],
        rates, yg, 24
    )
    # Year 1: PV dep = 25,000; Land dep = 4,000; total = 29,000
    assert annual_dep[0] == pytest.approx(29_000.0)


def test_repowering_bucket_capex_in_year10():
    """BESS repowering capex in bucket 6 contributes to depreciation."""
    yg = _year_groups(24, 2025, 1)
    capex = [[0.0] * 24 for _ in range(N_BUCKETS)]
    capex[6][12] = 50_000.0  # Repowering in month 12 (year 2)
    rates = [0.25] * N_BUCKETS
    annual_dep, _ = calculate_declining_balance(
        [0.0] * N_BUCKETS, capex, rates, yg, 24
    )
    # Year 1: no capex, no dep
    assert annual_dep[0] == pytest.approx(0.0)
    # Year 2: 50,000 × 0.25 = 12,500
    assert annual_dep[1] == pytest.approx(12_500.0)


def test_all_seven_buckets_contribute():
    """Capex in every bucket → all contribute to total depreciation."""
    yg = _year_groups(12, 2025, 1)
    capex = [[0.0] * 12 for _ in range(N_BUCKETS)]
    for b in range(N_BUCKETS):
        capex[b][0] = 10_000.0
    rates = [0.25, 0.25, 0.25, 0.25, 0.04, 0.25, 0.25]
    annual_dep, _ = calculate_declining_balance(
        [0.0] * N_BUCKETS, capex, rates, yg, 12
    )
    # 6 buckets × 10k × 0.25 + 1 bucket × 10k × 0.04 = 15,000 + 400 = 15,400
    assert annual_dep[0] == pytest.approx(15_400.0)


def test_calculate_with_seven_buckets_end_to_end():
    """Full calculate() with 7-bucket inputs."""
    n = 24
    capex = [[0.0] * n for _ in range(N_BUCKETS)]
    capex[0][0] = 100_000.0  # PV
    capex[4][0] = 50_000.0   # Land
    capex[6][12] = 30_000.0  # Repowering
    inp = Inputs(
        periods=n, start_year=2025, start_month=1,
        ebitda=[5_000.0] * n,
        total_interest=[100.0] * n,
        capex_by_bucket=capex,
        opening_balances=[0.0] * N_BUCKETS,
    )
    out = calculate(inp)
    assert isinstance(out, Outputs)
    assert out.total_tax_depreciation > 0
    assert len(out.tax_depreciation) == n


def test_bucket_names_count():
    """BUCKET_NAMES should have N_BUCKETS entries."""
    assert len(BUCKET_NAMES) == N_BUCKETS


# ---------------------------------------------------------------------------
# Unit: get_excel_formulas
# ---------------------------------------------------------------------------

def test_get_excel_formulas_keys():
    refs = {
        "opening_bal": "C10", "capex": "C11", "dep_rate": "B5",
        "ebitda": "H20", "interest": "H21", "unrestricted": "B6",
        "ebitda_pct": "B7", "tax_rate": "B8", "loss_limit": "B9",
        "loss_pct": "B10", "deductible_int": "H22",
        "annual_dep": "H23", "taxable_income": "H24", "allowed_interest": "H25",
    }
    formulas = get_excel_formulas(refs)
    for key in ("declining_balance_dep", "closing_balance", "ebitda_restriction",
                "allowed_interest", "non_deductible_interest",
                "taxable_income_gross", "tax_charge"):
        assert key in formulas
        assert formulas[key].startswith("=")
