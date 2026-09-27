"""Graph validation contracts for the Planning MCP

Covers deterministic input order, duplicate IDs, unknown predecessors,
cycles, per-row edge/structure bounds, connection allowlist and
normalization, bool lag rejection, and safe E_* errors.

Uses validate_planning_rows directly plus a tiny fake estimator; SAMPO
execution is avoided except for pre-import validation paths.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_adapter import graph_adapter as ga  # noqa: E402
from planning_adapter.config import (  # noqa: E402
    ERROR_CODES,
    E_CYCLE_DETECTED,
    E_DUPLICATE_ID,
    E_EDGE_INVALID,
    E_EMPTY_ROWS,
    E_LIMIT_EDGES,
    E_LIMIT_ROWS,
    E_LIMIT_STRUCTURE,
    E_RESOURCE_MODEL_MISSING,
    E_UNKNOWN_PREDECESSOR,
    PLANNING_MAX_EDGES_PER_ROW,
    PLANNING_MAX_STRUCTURE_ITEMS,
)
from planning_adapter.models import PlanningEdge, PlanningRow  # noqa: E402

# Source packages are packaged under planning/sources and imported through the
# adapter; this suite keeps the graph test independent from those packages.


def mk(aid: str, edges: list | None = None, structure: list | None = None) -> PlanningRow:
    """Build a minimal valid row with the given id."""
    return PlanningRow(
        activity_id=aid,
        activity_name=f"name-{aid}",
        volume=1.0,
        measurement="pcs",
        structure=structure if structure is not None else [],
        edges=edges if edges is not None else [],  # type: ignore[arg-type]
        enriched=None,
    )


def code(exc: BaseException) -> str:
    """Return the safe E_* prefix of an error message."""
    return str(exc).split(":")[0].strip()


class FakeEstimator:
    """Tiny source-compatible estimator without SAMPO imports."""

    def find_work_resources(self, model_name, work_volume, resource_name=None):
        """Return empty requirements deterministically."""
        return []

    def estimate_time(self, work_unit, worker_list):
        """Return a fixed duration marker."""
        return object()


def test_deterministic_input_order() -> None:
    """Normalized edges follow input row order, not sorted order."""
    fwd = [mk("A"), mk("B", edges=[("A", "FS", 0)]), mk("C", edges=[("B", "FS", 1)])]
    edges = ga.validate_planning_rows(fwd)
    assert [(e.predecessor_id, e.successor_id) for e in edges] == [("A", "B"), ("B", "C")]
    rev = [mk("C", edges=[("B", "FS", 1)]), mk("B", edges=[("A", "FS", 0)]), mk("A")]
    edges_rev = ga.validate_planning_rows(rev)
    assert [(e.predecessor_id, e.successor_id) for e in edges_rev] == [("B", "C"), ("A", "B")]


def test_duplicate_ids_rejected() -> None:
    """Duplicate activity_id values fail with E_DUPLICATE_ID."""
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A"), mk("A")])
    assert code(ctx.value) == E_DUPLICATE_ID
    assert ctx.value.__cause__ is None


def test_unknown_predecessor_rejected() -> None:
    """Edges pointing outside the known id set fail safely."""
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A"), mk("B", edges=[("MISSING", "FS", 0)])])
    assert code(ctx.value) == E_UNKNOWN_PREDECESSOR


def test_cycles_rejected() -> None:
    """Two-node cycles and self loops fail with E_CYCLE_DETECTED."""
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A", edges=[("B", "FS", 0)]), mk("B", edges=[("A", "FS", 0)])])
    assert code(ctx.value) == E_CYCLE_DETECTED
    with pytest.raises(ValueError) as ctx2:
        ga.validate_planning_rows([mk("A", edges=[("A", "FS", 0)])])
    assert code(ctx2.value) == E_CYCLE_DETECTED


def test_empty_rows_rejected() -> None:
    """Empty input fails with E_EMPTY_ROWS."""
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([])
    assert code(ctx.value) == E_EMPTY_ROWS


def test_per_row_edge_bound() -> None:
    """More than PLANNING_MAX_EDGES_PER_ROW edges fails without truncate."""
    many = [("A1", "FS", 0)] * (PLANNING_MAX_EDGES_PER_ROW + 1)
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A1", edges=many)])
    assert code(ctx.value) == E_LIMIT_EDGES
    ok = [("A1", "FS", 0)] * PLANNING_MAX_EDGES_PER_ROW
    # Self-loop at the bound still reaches cycle check, proving bound passed.
    with pytest.raises(ValueError) as ctx2:
        ga.validate_planning_rows([mk("A1", edges=ok)])
    assert code(ctx2.value) == E_CYCLE_DETECTED


def test_per_row_structure_bound() -> None:
    """More than PLANNING_MAX_STRUCTURE_ITEMS items fails safely."""
    many = [(f"C{i}", f"N{i}", 1, 1) for i in range(PLANNING_MAX_STRUCTURE_ITEMS + 1)]
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A1", structure=many)])
    assert code(ctx.value) == E_LIMIT_STRUCTURE


def test_rows_bound() -> None:
    """More than 200 rows fails with E_LIMIT_ROWS."""
    rows = [mk(f"A{i:04d}") for i in range(201)]
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows(rows)
    assert code(ctx.value) == E_LIMIT_ROWS


def test_connection_allowlist_and_normalization() -> None:
    """FS/SS/FF normalize; SF fails at graph; unknown types fail at model."""
    rows = [mk("A"), mk("B", edges=[("A", "FS", 2)]), mk("C", edges=[("A", "SS", 0), ("B", "FF", -1)])]
    edges = ga.validate_planning_rows(rows)
    assert [(e.connection_type, e.lag) for e in edges] == [("FS", 2), ("SS", 0), ("FF", -1)]
    assert all(isinstance(e, PlanningEdge) for e in edges)
    # SF passes the input model for CSV compatibility but is rejected by graph.
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([mk("A"), mk("B", edges=[("A", "SF", 0)])])
    assert code(ctx.value) == E_EDGE_INVALID
    # Unknown literals never reach graph: the frozen input model rejects them.
    for bad in ("XX", "fs", ""):
        with pytest.raises(ValidationError):
            mk("B", edges=[("A", bad, 0)])


def test_bool_lag_rejected() -> None:
    """Bool lag fails at model and graph layers with safe codes."""
    with pytest.raises(ValidationError):
        PlanningEdge(predecessor_id="A", successor_id="B", connection_type="FS", lag=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PlanningRow(activity_id="B", activity_name="n", volume=1.0, measurement="m", edges=[("A", "FS", True)])  # type: ignore[list-item]
    stub = SimpleNamespace(activity_id="A", edges=[("A", "FS", True)], structure=[],
                            volume=1.0, measurement="pcs", enriched=None)
    with pytest.raises(ValueError) as ctx:
        ga.validate_planning_rows([stub])  # type: ignore[list-item]
    assert code(ctx.value) == E_EDGE_INVALID


def test_safe_error_codes_and_no_leak() -> None:
    """All validation errors use closed E_* codes without raw secrets."""
    secret = "SECRET-9f3a"
    cases = [
        ([mk("A"), mk("A")], E_DUPLICATE_ID),
        ([mk("A"), mk("B", edges=[(secret, "FS", 0)])], E_UNKNOWN_PREDECESSOR),
        ([mk("A", edges=[("A", "FS", 0)])], E_CYCLE_DETECTED),
    ]
    for rows, expected in cases:
        with pytest.raises(ValueError) as ctx:
            ga.validate_planning_rows(rows)  # type: ignore[arg-type]
        assert code(ctx.value) == expected
        assert code(ctx.value) in ERROR_CODES
        assert secret not in str(ctx.value) or expected == E_UNKNOWN_PREDECESSOR and secret not in str(ctx.value)
        assert ctx.value.__cause__ is None


def test_fake_estimator_satisfies_protocol() -> None:
    """Tiny fake estimator passes the runtime protocol check."""
    assert isinstance(FakeEstimator(), ga.WorkEstimatorProtocol)


def test_build_rejects_bad_estimator_without_sampo() -> None:
    """Missing estimator fails before any SAMPO import."""
    with pytest.raises(RuntimeError) as ctx:
        ga.build_work_graph([mk("A")], None)  # type: ignore[arg-type]
    assert str(ctx.value) == E_RESOURCE_MODEL_MISSING
    with pytest.raises(RuntimeError) as ctx2:
        ga.build_work_graph([mk("A")], object())  # type: ignore[arg-type]
    assert str(ctx2.value) == E_RESOURCE_MODEL_MISSING


def test_build_propagates_validation_before_sampo() -> None:
    """Row validation errors surface before SAMPO graph construction."""
    with pytest.raises(ValueError) as ctx:
        ga.build_work_graph([mk("A"), mk("A")], FakeEstimator())  # type: ignore[arg-type]
    assert code(ctx.value) == E_DUPLICATE_ID
