"""
assembly/debt_solver.py

Iterative debt sizing solver for sculpted debt.

Resolves the circularity:
  debt size -> interest -> tax shield -> CFADS -> sculpted debt -> debt size

Wraps engine.run() in a convergence loop. Typically converges in 3-5 iterations.

Usage:
    from assembly.debt_solver import solve_sculpted_debt

    result, iterations, delta = solve_sculpted_debt(
        base_config,           # ProjectConfig WITHOUT debt_sculpt
        sculpt_template,       # DEBT_SCULPT_001.Inputs template (cfads will be overwritten)
        tolerance=1.0,         # kEUR convergence threshold on facility size
        max_iterations=10,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from assembly.engine import ProjectConfig, run, AssemblyResult
import modules.debt.DEBT_SCULPT_001 as DEBT_SCULPT_001


@dataclass
class SolverResult:
    result: AssemblyResult
    config: ProjectConfig
    iterations: int
    final_delta: float
    converged: bool
    facility_history: list[float]


def _extract_cfads(result: AssemblyResult, n: int) -> list[float]:
    """Extract CFADS from engine result: EBITDA - tax_paid."""
    out = result.outputs
    gross_rev = [0.0] * n
    total_opex = [0.0] * n

    for mod_id in ("REV_001", "REV_002", "REV_003"):
        mod = out.get(mod_id)
        if mod:
            for p in range(n):
                gross_rev[p] += mod.net_revenue[p]

    for mod_id in ("OPEX_001", "OPEX_002", "OPEX_003"):
        mod = out.get(mod_id)
        if mod:
            for p in range(n):
                total_opex[p] += mod.total_opex[p]

    ebitda = [gross_rev[p] - total_opex[p] for p in range(n)]

    tax_out = out.get("TAX_001") or out.get("TAX_DE_001") or out.get("TAX_LT_001")
    tax_paid = tax_out.tax_paid if tax_out else [0.0] * n

    return [ebitda[p] - tax_paid[p] for p in range(n)]


def solve_sculpted_debt(
    base_config: ProjectConfig,
    sculpt_template: DEBT_SCULPT_001.Inputs,
    tolerance: float = 1.0,
    max_iterations: int = 10,
    verbose: bool = True,
) -> SolverResult:
    """
    Iteratively size sculpted debt until facility converges.

    Args:
        base_config: ProjectConfig WITHOUT debt_sculpt (or with placeholder).
            Must have revenue, opex, capex, tax already configured.
        sculpt_template: DEBT_SCULPT_001.Inputs with all fields set EXCEPT
            dscr_streams[*].cfads — these will be overwritten each iteration.
        tolerance: Convergence threshold on abs(facility_n - facility_n-1).
        max_iterations: Safety limit on iterations.
        verbose: Print iteration progress.

    Returns:
        SolverResult with the converged engine result, final config,
        iteration count, and convergence delta.
    """
    n = sculpt_template.periods
    prev_facility = 0.0
    facility_history = []

    for iteration in range(1, max_iterations + 1):
        # Run engine (without sculpt on first pass, with sculpt on subsequent)
        if iteration == 1:
            config = base_config.model_copy(update={"debt_sculpt": None})
        else:
            config = base_config.model_copy(update={"debt_sculpt": sculpt_inputs})

        result = run(config)

        # Extract CFADS from this iteration's result
        cfads = _extract_cfads(result, n)

        # Update sculpt template with new CFADS
        updated_streams = []
        for stream in sculpt_template.dscr_streams:
            updated_streams.append(stream.model_copy(update={"cfads": cfads}))

        sculpt_inputs = sculpt_template.model_copy(update={
            "dscr_streams": updated_streams,
        })

        # Also update constraint scenarios if present
        if sculpt_template.constraint_scenarios:
            updated_constraints = []
            for cs in sculpt_template.constraint_scenarios:
                cs_streams = []
                for s in cs.dscr_streams:
                    cs_streams.append(s.model_copy(update={"cfads": cfads}))
                updated_constraints.append(cs.model_copy(update={"dscr_streams": cs_streams}))
            sculpt_inputs = sculpt_inputs.model_copy(update={
                "constraint_scenarios": updated_constraints,
            })

        # Run with sculpted debt to get facility size
        config_with_sculpt = base_config.model_copy(update={"debt_sculpt": sculpt_inputs})
        result = run(config_with_sculpt)

        sculpt_out = result.outputs.get("DEBT_SCULPT_001")
        current_facility = sculpt_out.total_debt if sculpt_out else 0.0
        delta = abs(current_facility - prev_facility)
        facility_history.append(current_facility)

        if verbose:
            print(f"  Iteration {iteration}: facility={current_facility:,.1f}  "
                  f"delta={delta:,.1f}  (tol={tolerance})")

        if delta < tolerance and iteration > 1:
            if verbose:
                print(f"  Converged in {iteration} iterations.")
            return SolverResult(
                result=result,
                config=config_with_sculpt,
                iterations=iteration,
                final_delta=delta,
                converged=True,
                facility_history=facility_history,
            )

        prev_facility = current_facility

    if verbose:
        print(f"  WARNING: did not converge after {max_iterations} iterations "
              f"(delta={delta:,.1f})")

    return SolverResult(
        result=result,
        config=config_with_sculpt,
        iterations=max_iterations,
        final_delta=delta,
        converged=False,
        facility_history=facility_history,
    )
