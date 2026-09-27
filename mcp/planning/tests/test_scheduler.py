"""Scheduler option matrix: algorithms x objectives without executing SAMPO."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import planning_adapter.scheduler_adapter as sa  # noqa: E402

_NON_GENETIC = ["topological", "randomized_topological", "heft", "heft_between"]
_FAKE_ESTIMATOR = object()


@pytest.mark.parametrize("algo", _NON_GENETIC)
@pytest.mark.parametrize("objective", ["deadline", "resource_optimization"])
def test_nongenetic_rejects_non_mintime(algo, objective):
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler(algo, objective, _FAKE_ESTIMATOR)


@pytest.mark.parametrize("algo", _NON_GENETIC)
def test_nongenetic_rejects_genetic_options(algo):
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler(algo, "min_time", _FAKE_ESTIMATOR, genetic_generations=5)
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler(algo, "min_time", _FAKE_ESTIMATOR, deadline=date(2026, 12, 31))


def test_nongenetic_rejects_missing_estimator():
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler("heft", "min_time", None)


def test_unknown_algorithm_rejected():
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.resolve_scheduler_class("nope")
    assert sa.resolve_scheduler_class("heft") is not None


@pytest.mark.parametrize("bad", [0, 201, True, "7", 7.0, -3])
def test_genetic_generations_bounds(bad):
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler("genetic", "min_time", _FAKE_ESTIMATOR, genetic_generations=bad)


def test_genetic_deadline_needs_dates():
    with pytest.raises(ValueError, match="E_DEADLINE_INVALID"):
        sa.build_scheduler("genetic", "deadline", _FAKE_ESTIMATOR)
    with pytest.raises(ValueError, match="E_DEADLINE_INVALID"):
        sa.build_scheduler(
            "genetic", "deadline", _FAKE_ESTIMATOR,
            deadline=date(2026, 1, 1), project_start_date=date(2026, 2, 1),
        )


def test_genetic_unknown_objective_rejected():
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_scheduler("genetic", "nope", _FAKE_ESTIMATOR)


@pytest.mark.parametrize("graph,scaler", [(None, 1), (_FAKE_ESTIMATOR, True), (_FAKE_ESTIMATOR, 0), (_FAKE_ESTIMATOR, -1), (_FAKE_ESTIMATOR, "5")])
def test_contractors_reject_bad_inputs(graph, scaler):
    with pytest.raises(ValueError, match="E_OPTIONS_INVALID"):
        sa.build_contractors(graph, scaler)
