"""Deterministic Planning validation and SAMPO WorkGraph boundary"""

import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from sampo.schemas import Worker, WorkUnit
    from sampo.schemas.graph import WorkGraph
    from sampo.schemas.time import Time

from .config import (
    E_CYCLE_DETECTED,
    E_DUPLICATE_ID,
    E_EDGE_INVALID,
    E_EMPTY_ROWS,
    E_ENRICHED_INVALID,
    E_INTERNAL,
    E_LIMIT_EDGES,
    E_LIMIT_ROWS,
    E_LIMIT_STRUCTURE,
    E_MEASUREMENT_MISSING,
    E_RESOURCE_MODEL_MISSING,
    E_UNKNOWN_PREDECESSOR,
    E_VOLUME_INVALID,
    PLANNING_MAX_EDGES_PER_ROW,
    PLANNING_MAX_ROWS,
    PLANNING_MAX_STRUCTURE_ITEMS,
)
from .models import EnrichedUnit, PlanningEdge, PlanningRow

__all__ = (
    "WorkEstimatorProtocol",
    "GraphBuildResult",
    "validate_planning_rows",
    "build_work_graph",
)


# No SF: SAMPO's EdgeType has no start-finish link; SF is input-compatible
# but rejected here with E_EDGE_INVALID instead of SAMPO's raw ValueError.
_NORMALIZED_TYPES = frozenset({"FS", "SS", "FF"})

# Empty-history frame columns, mirrored from the backend pipeline
_HISTORY_COLUMNS = (
    "work_id",
    "work_name",
    "granular_name",
    "first_day",
    "last_day",
    "upper_works",
)


@runtime_checkable
class WorkEstimatorProtocol(Protocol):
    """Source-compatible estimator boundary without SAMPO runtime imports."""

    def find_work_resources(
        self,
        model_name: dict[str, str],
        work_volume: float,
        resource_name: list[str] | None = None,
    ) -> list[Any]:
        """
        Return worker requirements for a model name and volume.

        Args:
            model_name: Normalized mapping with string values only
                (``name``/``category``/``measurement``).
            work_volume: Work volume in the row's measurement units.
            resource_name: Unused; kept for the SAMPO estimator signature.

        Returns:
            SAMPO worker requirements (concrete type lives in SAMPO).
        """
        ...

    def estimate_time(self, work_unit: "WorkUnit", worker_list: list["Worker"]) -> "Time":
        """Estimate duration for a work unit with assigned workers.

        Args:
            work_unit: SAMPO work unit to estimate.
            worker_list: Workers assigned to the unit.

        Returns:
            SAMPO duration object.
        """
        ...


@dataclass(frozen=True)
class GraphBuildResult:
    """Frozen WorkGraph build output preserving deterministic edge order."""

    work_graph: "WorkGraph"
    edges: tuple[PlanningEdge, ...]


def _is_present_name(value: object) -> bool:
    """Check non-blank string without trimming the stored value."""
    return isinstance(value, str) and value.strip() != ""


def validate_planning_rows(rows: Sequence[PlanningRow]) -> tuple[PlanningEdge, ...]:
    """Validate rows and return normalized edges preserving order.

    Checks run in fixed order: limits, duplicate ids, edge shapes (``SF``
    rejected), known predecessors, cycles (Kahn), values.

    Raises:
        ValueError: Safe ``E_*`` code for the first violation found.
        RuntimeError: ``E_INTERNAL`` on unexpected failures.
    """
    try:
        try:
            count = len(rows)
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        if count == 0:
            raise ValueError(E_EMPTY_ROWS)
        for row in rows:
            try:
                edge_count = len(row.edges)
                struct_count = len(row.structure)
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            if count > PLANNING_MAX_ROWS:
                raise ValueError(E_LIMIT_ROWS)
            if edge_count > PLANNING_MAX_EDGES_PER_ROW:
                raise ValueError(E_LIMIT_EDGES)
            if struct_count > PLANNING_MAX_STRUCTURE_ITEMS:
                raise ValueError(E_LIMIT_STRUCTURE)
        seen: set[object] = set()
        for row in rows:
            try:
                key = row.activity_id
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            try:
                if key in seen:
                    raise ValueError(E_DUPLICATE_ID)
                seen.add(key)
            except ValueError:
                raise
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
        for row in rows:
            for edge in row.edges:
                if not isinstance(edge, (list, tuple)) or len(edge) != 3:
                    raise ValueError(E_EDGE_INVALID)
                predecessor, conn_type, lag = edge[0], edge[1], edge[2]
                if not isinstance(predecessor, str) or not predecessor.strip():
                    raise ValueError(E_EDGE_INVALID)
                if conn_type not in _NORMALIZED_TYPES:
                    raise ValueError(E_EDGE_INVALID)
                if type(lag) is not int:
                    raise ValueError(E_EDGE_INVALID)
        try:
            known = {row.activity_id for row in rows}
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        for row in rows:
            for edge in row.edges:
                predecessor = edge[0]
                try:
                    missing = predecessor not in known
                except Exception:
                    raise RuntimeError(E_INTERNAL) from None
                if missing:
                    raise ValueError(E_UNKNOWN_PREDECESSOR)
        try:
            adjacency: dict[object, list[object]] = {row.activity_id: [] for row in rows}
            indegree: dict[object, int] = {row.activity_id: 0 for row in rows}
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        for row in rows:
            successor = row.activity_id
            for edge in row.edges:
                predecessor = edge[0]
                adjacency[predecessor].append(successor)
                indegree[successor] += 1
        queue: deque[object] = deque(row.activity_id for row in rows if indegree[row.activity_id] == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for successor in adjacency[current]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        if visited != count:
            raise ValueError(E_CYCLE_DETECTED)
        for row in rows:
            volume = row.volume
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise ValueError(E_VOLUME_INVALID)
            if not math.isfinite(volume) or volume < 0:
                raise ValueError(E_VOLUME_INVALID)
            measurement = row.measurement
            if not isinstance(measurement, str) or not measurement.strip():
                raise ValueError(E_MEASUREMENT_MISSING)
            enriched = row.enriched
            if enriched is not None:
                if not isinstance(enriched, EnrichedUnit):
                    raise ValueError(E_ENRICHED_INVALID)
                distance = enriched.distance
                if distance is not None:
                    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                        raise ValueError(E_ENRICHED_INVALID)
                    if not math.isfinite(distance):
                        raise ValueError(E_ENRICHED_INVALID)
                if enriched.rank is not None and type(enriched.rank) is not int:
                    raise ValueError(E_ENRICHED_INVALID)
                for field in (enriched.code, enriched.name, enriched.measurement, enriched.category):
                    if field is not None and not isinstance(field, str):
                        raise ValueError(E_ENRICHED_INVALID)
        normalized: list[PlanningEdge] = []
        for row in rows:
            successor = row.activity_id
            for edge in row.edges:
                try:
                    normalized.append(
                        PlanningEdge(
                            predecessor_id=edge[0],
                            successor_id=successor,
                            connection_type=edge[1],
                            lag=edge[2],
                        )
                    )
                except Exception:
                    raise ValueError(E_EDGE_INVALID) from None
        return tuple(normalized)
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None


def _model_name_str(row: PlanningRow) -> str:
    """Build source-compatible serialized model_name for one row."""
    enriched = row.enriched
    if _is_present_name(getattr(enriched, "name", None)):
        name = enriched.name
    else:
        name = row.activity_name
    if _is_present_name(getattr(enriched, "measurement", None)):
        measurement = enriched.measurement
    else:
        measurement = row.measurement
    raw_category = getattr(enriched, "category", None)
    category = raw_category if isinstance(raw_category, str) else ""
    raw_code = getattr(enriched, "code", None)
    code = raw_code if isinstance(raw_code, str) else ""
    return str({"code": code, "name": str(name), "category": category, "measurement": str(measurement)})


def build_work_graph(
    rows: Sequence[PlanningRow], estimator: WorkEstimatorProtocol
) -> GraphBuildResult:
    """Build the SAMPO graph from validated rows via the source frame shape.

    Args:
        rows: Planning rows in deterministic input order.
        estimator: Runtime-checked estimator boundary.

    Raises:
        ValueError: Safe ``E_*`` code from row validation.
        RuntimeError: ``E_RESOURCE_MODEL_MISSING`` for a foreign estimator,
            ``E_INTERNAL`` on unexpected failures.
    """
    try:
        if estimator is None or not isinstance(estimator, WorkEstimatorProtocol):
            raise RuntimeError(E_RESOURCE_MODEL_MISSING) from None
        edges = validate_planning_rows(rows)
        # Adapter defaults rooted in the source: priority 1..n mirrors normalized
        # 1..k; omit resource columns for estimator refill; structure has
        # no source frame column so it is validated only.
        frame_rows: list[dict[str, object]] = []
        for position, row in enumerate(rows):
            predecessor_ids = ",".join(edge[0] for edge in row.edges)
            connection_types = ",".join(edge[1] for edge in row.edges)
            lags = ",".join(str(edge[2]) for edge in row.edges)
            frame_rows.append(
                {
                    "activity_id": row.activity_id,
                    "activity_name": row.activity_name,
                    "volume": row.volume,
                    "predecessor_ids": predecessor_ids,
                    "connection_types": connection_types,
                    "lags": lags,
                    "priority": position + 1,
                    "model_name": _model_name_str(row),
                }
            )
        try:
            import pandas as pd

            from sampo.userinput import CSVParser
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        try:
            frame = pd.DataFrame(frame_rows)
            history = pd.DataFrame(columns=list(_HISTORY_COLUMNS))
            works_info = CSVParser.read_graph_info(
                project_info=frame,
                history_data=history,
                all_connections=True,
                change_connections_info=False,
            )
            work_graph = CSVParser.work_graph(
                works_info=works_info,
                work_resource_estimator=estimator,
            )
        except ValueError:
            raise
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        return GraphBuildResult(work_graph=work_graph, edges=edges)
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None
