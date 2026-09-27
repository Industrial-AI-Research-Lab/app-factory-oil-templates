"""SAMPO scheduler and pipeline adapter for the Planning MCP.

Builds source-compatible schedulers and contractors, then runs the
SchedulingPipeline in the backend order. SAMPO imports are runtime here
because scheduler classes are instantiated, not just referenced.
"""

from datetime import date
from typing import Any

import pandas as pd
from sampo.api.genetic_api import ScheduleGenerationScheme
from sampo.generator.environment import ContractorGenerationMethod, get_contractor_by_wg
from sampo.pipeline import SchedulingPipeline
from sampo.pipeline.lag_optimization import LagOptimizationStrategy
from sampo.scheduler.base import Scheduler
from sampo.scheduler.genetic.base import GeneticScheduler
from sampo.scheduler.genetic.operators import SumOfResourcesPeaksFitness, TimeFitness
from sampo.scheduler.heft.base import HEFTBetweenScheduler, HEFTScheduler
from sampo.scheduler.topological.base import RandomizedTopologicalScheduler, TopologicalScheduler
from sampo.schemas import Contractor, ScheduledProject, WorkTimeEstimator
from sampo.schemas.graph import WorkGraph
from sampo.schemas.time import Time

from .config import (
    ALL_CONNECTIONS,
    E_DEADLINE_INVALID,
    E_NO_CONTRACTORS,
    E_OPTIONS_INVALID,
    ELIMINATE_RESOURCE_PEAKS_BY_OBJECTIVE,
    MAX_GENETIC_GENERATIONS,
    MIN_GENETIC_GENERATIONS,
    RESTRUCTURE_PARALLEL_DEFAULT,
)
from .models import ScheduleProjectRequest

__all__ = (
    "SCHEDULER_BY_ALGORITHM",
    "resolve_scheduler_class",
    "build_scheduler",
    "build_contractors",
    "run_scheduling_pipeline",
)

# Algorithm literals to SAMPO scheduler classes, same order as the backend.
SCHEDULER_BY_ALGORITHM: dict[str, type] = {
    "topological": TopologicalScheduler,
    "randomized_topological": RandomizedTopologicalScheduler,
    "heft": HEFTScheduler,
    "heft_between": HEFTBetweenScheduler,
    "genetic": GeneticScheduler,
}

# Single synthetic contractor id: the tool always schedules one contractor pool.
_CONTRACTOR_ID = "planning-contractor"

# Empty-history frame: no past works, so the pipeline plans from scratch.
_EMPTY_HISTORY_COLUMNS = (
    "work_id",
    "work_name",
    "granular_name",
    "first_day",
    "last_day",
    "upper_works",
)


def resolve_scheduler_class(algorithm: str) -> type:
    """Return the SAMPO scheduler class for a canonical algorithm.

    Raises:
        ValueError: ``E_OPTIONS_INVALID`` for unknown algorithms.
    """
    try:
        return SCHEDULER_BY_ALGORITHM[algorithm]
    except KeyError:
        raise ValueError(E_OPTIONS_INVALID) from None


def _require_valid_generations(genetic_generations: Any) -> None:
    """Validate optional Genetic generation count against config bounds.

    ``Any`` because the value arrives unchecked from the request model;
    bools and non-ints are rejected before the range check.
    """
    if genetic_generations is None:
        return
    if isinstance(genetic_generations, bool) or not isinstance(genetic_generations, int):
        raise ValueError(E_OPTIONS_INVALID) from None
    if not MIN_GENETIC_GENERATIONS <= genetic_generations <= MAX_GENETIC_GENERATIONS:
        raise ValueError(E_OPTIONS_INVALID) from None


def build_scheduler(
    algorithm: str,
    objective: str,
    work_estimator: WorkTimeEstimator,
    genetic_generations: int | None = None,
    deadline: date | None = None,
    project_start_date: date | None = None,
) -> Scheduler:
    """Build a source scheduler instance with validated options.

    Non-Genetic accepts ``min_time`` only. Genetic uses Serial SGS,
    the supplied generation count, fitness by objective, and
    ``optimize_resources`` by objective mapping.

    Returns:
        SAMPO scheduler instance (concrete class varies by algorithm).

    Raises:
        ValueError: ``E_OPTIONS_INVALID``/``E_DEADLINE_INVALID`` on bad combos.
        RuntimeError: ``E_SCHEDULING_FAILED`` when SAMPO refuses the options.
    """
    scheduler_cls = resolve_scheduler_class(algorithm)
    if work_estimator is None:
        raise ValueError(E_OPTIONS_INVALID) from None

    if scheduler_cls is not GeneticScheduler:
        if objective != "min_time":
            raise ValueError(E_OPTIONS_INVALID) from None
        if genetic_generations is not None or deadline is not None:
            raise ValueError(E_OPTIONS_INVALID) from None
        try:
            return scheduler_cls(work_estimator=work_estimator)
        except Exception:
            raise RuntimeError(E_SCHEDULING_FAILED) from None

    _require_valid_generations(genetic_generations)
    if objective not in ("min_time", "deadline", "resource_optimization"):
        raise ValueError(E_OPTIONS_INVALID) from None
    if objective != "deadline" and deadline is not None:
        raise ValueError(E_OPTIONS_INVALID) from None

    # Serial SGS plus optional generation count, as in the backend.
    scheduler_kwargs: dict[str, Any] = {"sgs_type": ScheduleGenerationScheme.Serial}
    if genetic_generations is not None:
        scheduler_kwargs["number_of_generation"] = genetic_generations

    deadline_days: int | None = None
    if objective == "min_time":
        scheduler_kwargs["fitness_constructor"] = TimeFitness()
    elif objective == "deadline":
        if deadline is None or project_start_date is None:
            raise ValueError(E_DEADLINE_INVALID) from None
        if not isinstance(deadline, date) or not isinstance(project_start_date, date):
            raise ValueError(E_DEADLINE_INVALID) from None
        deadline_days = (deadline - project_start_date).days
        if deadline_days < 0:
            raise ValueError(E_DEADLINE_INVALID) from None
        # Peak fitness without resource optimization, deadline enforced below.
        scheduler_kwargs["fitness_constructor"] = SumOfResourcesPeaksFitness()
    else:
        # Peak fitness with resource optimization.
        scheduler_kwargs["fitness_constructor"] = SumOfResourcesPeaksFitness()
    scheduler_kwargs["optimize_resources"] = ELIMINATE_RESOURCE_PEAKS_BY_OBJECTIVE[objective]
    scheduler_kwargs["work_estimator"] = work_estimator

    try:
        scheduler = scheduler_cls(**scheduler_kwargs)
    except Exception:
        raise RuntimeError(E_SCHEDULING_FAILED) from None
    if deadline_days is not None:
        try:
            scheduler.set_deadline(Time(deadline_days))
        except Exception:
            raise RuntimeError(E_SCHEDULING_FAILED) from None
    return scheduler


def build_contractors(work_graph: WorkGraph, scaler: int) -> list[Contractor]:
    """Generate a single contractor from the graph via SAMPO's AVG method.

    Raises:
        ValueError: ``E_OPTIONS_INVALID`` for a missing graph or bad scaler.
        RuntimeError: ``E_SCHEDULING_FAILED`` when SAMPO generation fails.
    """
    if work_graph is None:
        raise ValueError(E_OPTIONS_INVALID) from None
    if isinstance(scaler, bool) or not isinstance(scaler, int) or scaler <= 0:
        raise ValueError(E_OPTIONS_INVALID) from None
    try:
        contractor = get_contractor_by_wg(
            wg=work_graph,
            scaler=scaler,
            method=ContractorGenerationMethod.AVG,
            contractor_id=_CONTRACTOR_ID,
        )
    except Exception:
        raise RuntimeError(E_SCHEDULING_FAILED) from None
    return [contractor]


def run_scheduling_pipeline(
    work_graph: WorkGraph,
    work_estimator: WorkTimeEstimator,
    request: ScheduleProjectRequest,
    project_start_date: date,
    contractor_scaler: int,
) -> ScheduledProject:
    """Execute the backend pipeline order: graph, contractors, estimator, history.

    Raises:
        ValueError: ``E_OPTIONS_INVALID`` on missing inputs.
        RuntimeError: ``E_NO_CONTRACTORS`` when empty,
            ``E_SCHEDULING_FAILED`` when SAMPO fails.
    """
    if work_graph is None or work_estimator is None or request is None:
        raise ValueError(E_OPTIONS_INVALID) from None
    if not isinstance(project_start_date, date):
        raise ValueError(E_OPTIONS_INVALID) from None

    algorithm = getattr(request, "algorithm", None)
    objective = getattr(request, "optimization_objective", None)
    genetic_generations = getattr(request, "genetic_generations", None)
    deadline = getattr(request, "deadline", None)

    scheduler = build_scheduler(
        algorithm,
        objective,
        work_estimator,
        genetic_generations=genetic_generations,
        deadline=deadline,
        project_start_date=project_start_date,
    )
    contractors = build_contractors(work_graph, contractor_scaler)
    if len(contractors) == 0:
        raise RuntimeError(E_NO_CONTRACTORS) from None

    if hasattr(work_graph, "to_frame"):
        try:
            wg_frame = work_graph.to_frame()
        except Exception:
            raise RuntimeError(E_SCHEDULING_FAILED) from None
    else:
        wg_frame = work_graph
    history_frame = pd.DataFrame(columns=list(_EMPTY_HISTORY_COLUMNS))
    # Parallel restructuring flag drives the lag strategy.
    lag_strategy = (
        LagOptimizationStrategy.TRUE
        if RESTRUCTURE_PARALLEL_DEFAULT
        else LagOptimizationStrategy.FALSE
    )

    try:
        # Backend pipeline order: graph, contractors, estimator, history.
        scheduled = (
            SchedulingPipeline.create()
            .wg(wg=wg_frame, all_connections=ALL_CONNECTIONS)
            .contractors(contractors=contractors)
            .work_estimator(work_estimator)
            .history(history_frame)
            .lag_optimize(lag_strategy)
            .schedule(scheduler)
            .finish()
        )
    except Exception:
        raise RuntimeError(E_SCHEDULING_FAILED) from None
    try:
        return scheduled[0]
    except Exception:
        raise RuntimeError(E_SCHEDULING_FAILED) from None
