"""Single deterministic ``schedule_project`` entry point over the adapters.

Pipeline order: options, start date, CSV rows, model storage, estimator,
work graph, scheduler, then conversion to the ``ScheduleResult`` contract.
"""

import ast
import logging
import math
import time
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Callable

from .config import (
    ALLOWED_RESOURCE_MODELS,
    CONTRACTOR_SCALERS,
    DEFAULT_CONTRACTOR_SIZE,
    DEFAULT_INCLUDE_SAMPO_DUMP,
    DEFAULT_PROJECT_NAME,
    DEFAULT_RESOURCE_MODEL,
    ERROR_CODES,
    E_INTERNAL,
    E_OPTIONS_INVALID,
    E_SCHEDULING_FAILED,
)
from .estimator_adapter import FieldDevWorkEstimator
from .errors import PlanningRuntimeError, PlanningValidationError
from .graph_adapter import build_work_graph
from .input_loader import load_planning_rows
from .models import (
    ResourceAssignment,
    ScheduleProjectRequest,
    ScheduleResult,
    ScheduledWork,
)
from .resource_adapter import ResourceModelAdapter
from .scheduler_adapter import run_scheduling_pipeline
from .storage_adapter import load_model_storage

__all__ = ("schedule_project",)

logger = logging.getLogger(__name__)


def _is_domain_error(exc: BaseException) -> bool:
    """Check typed errors and legacy exact-code adapter exceptions.

    Legacy adapters raise plain errors with a single ``E_*`` code argument;
    both shapes pass through, everything else becomes ``E_INTERNAL``.
    """
    return isinstance(exc, (PlanningValidationError, PlanningRuntimeError)) or (
        len(exc.args) == 1 and exc.args[0] in ERROR_CODES
    )

def _resolve_start_date(start: date | None, provider: Callable[[], date] | None) -> date:
    """Resolve the project start date: explicit value, provider, or today.

    Raises:
        PlanningValidationError: ``E_OPTIONS_INVALID`` for a non-date value.
        RuntimeError: ``E_INTERNAL`` when the provider fails.
    """
    if start is not None:
        if type(start) is not date:
            raise PlanningValidationError(E_OPTIONS_INVALID, "invalid start date") from None
        return start
    if provider is not None:
        try:
            resolved = provider()
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        if type(resolved) is not date:
            raise RuntimeError(E_INTERNAL) from None
        return resolved
    return date.today()

def _resolve_scaler(contractor_size: str) -> int:
    """Map contractor size to the resource pool multiplier.

    Raises:
        PlanningValidationError: ``E_OPTIONS_INVALID`` for unknown sizes.
    """
    try:
        return CONTRACTOR_SCALERS[contractor_size]
    except Exception:
        raise PlanningValidationError(E_OPTIONS_INVALID, "invalid contractor size") from None

def _validate_options(resource_model: str, size: str, name: str, dump: bool) -> None:
    """Validate injectable options with safe codes only.

    Raises:
        PlanningValidationError: ``E_OPTIONS_INVALID`` on the first bad option.
    """
    if not isinstance(resource_model, str) or resource_model not in ALLOWED_RESOURCE_MODELS:
        raise PlanningValidationError(E_OPTIONS_INVALID, "invalid resource model") from None
    if not isinstance(size, str) or size not in CONTRACTOR_SCALERS:
        raise PlanningValidationError(E_OPTIONS_INVALID, "invalid contractor size") from None
    if not isinstance(name, str) or not name.strip():
        raise PlanningValidationError(E_OPTIONS_INVALID, "invalid project name") from None
    if type(dump) is not bool:
        raise PlanningValidationError(E_OPTIONS_INVALID, "invalid dump flag") from None

def _coerce_datetime(value: object) -> datetime:
    """Coerce schedule start/finish cells to datetime.

    Accepts pandas timestamps (via ``to_pydatetime``), datetimes, and dates.

    Raises:
        RuntimeError: ``E_INTERNAL`` for anything else, without raw values.
    """
    try:
        converter = getattr(value, "to_pydatetime", None)
        if callable(converter):
            converted = converter()
            if isinstance(converted, datetime):
                return converted
            raise RuntimeError(E_INTERNAL) from None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None
    raise RuntimeError(E_INTERNAL) from None

def _parse_resources(raw: object) -> list[ResourceAssignment]:
    """Parse worker cells (dict or its string form) to name-sorted assignments.

    Counts round up; every malformed shape becomes ``E_INTERNAL`` so raw
    cell contents never leak into errors.
    """
    try:
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            return []
        if isinstance(raw, dict):
            items: dict[Any, Any] = dict(raw)
        elif isinstance(raw, str):
            try:
                parsed = ast.literal_eval(raw)
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            if not isinstance(parsed, dict):
                raise RuntimeError(E_INTERNAL) from None
            items = parsed
        else:
            raise RuntimeError(E_INTERNAL) from None
        out: list[ResourceAssignment] = []
        for key, count in items.items():
            if not isinstance(key, str) or not key.strip():
                raise RuntimeError(E_INTERNAL) from None
            if isinstance(count, bool) or not isinstance(count, (int, float)):
                raise RuntimeError(E_INTERNAL) from None
            if not math.isfinite(count) or count < 0:
                raise RuntimeError(E_INTERNAL) from None
            try:
                out.append(ResourceAssignment(name=key, count=int(math.ceil(count))))
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
        out.sort(key=lambda item: item.name)
        return out
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None

def _convert_works(scheduled: object, names: dict[str, str], order: dict[str, int], start_dt: datetime) -> list[ScheduledWork]:
    """Convert the scheduled project via its datetime dataframe.

    Shape mirrors the backend schedule parser: ``task_id``/``start``/
    ``finish``/``contractor``/``workers`` rows. Synthetic ids are skipped;
    a frame with no known works, or a duplicated id, is a scheduling failure.

    Raises:
        RuntimeError: ``E_SCHEDULING_FAILED`` or ``E_INTERNAL``.
    """
    try:
        schedule = getattr(scheduled, "schedule", None)
        merge = getattr(schedule, "merged_stages_datetime_df", None)
        if schedule is None or not callable(merge):
            raise RuntimeError(E_INTERNAL) from None
        try:
            frame = merge(start_dt)
        except ValueError as exc:
            if _is_domain_error(exc):
                raise
            raise RuntimeError(E_INTERNAL) from None
        except RuntimeError as exc:
            if _is_domain_error(exc):
                raise
            raise RuntimeError(E_INTERNAL) from None
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        try:
            tuples = list(frame.itertuples(index=False))
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        staged: list[tuple[int, ScheduledWork]] = []
        scheduled_ids: set[str] = set()
        for row in tuples:
            task_id = getattr(row, "task_id", None)
            if not isinstance(task_id, str) or task_id not in names:
                continue
            if task_id in scheduled_ids:
                raise RuntimeError(E_SCHEDULING_FAILED) from None
            scheduled_ids.add(task_id)
            try:
                start = _coerce_datetime(getattr(row, "start", None))
                finish = _coerce_datetime(getattr(row, "finish", None))
                resources = _parse_resources(getattr(row, "workers", None))
            except (ValueError, RuntimeError):
                raise
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            duration = (finish - start).total_seconds() / 86400.0
            if not math.isfinite(duration) or duration < 0:
                raise RuntimeError(E_INTERNAL) from None
            raw_contractor = getattr(row, "contractor", None)
            if raw_contractor is None:
                contractor: str | None = None
            elif isinstance(raw_contractor, str) and raw_contractor.strip() == "":
                contractor = None
            else:
                try:
                    contractor = str(raw_contractor)
                except Exception:
                    raise RuntimeError(E_INTERNAL) from None
            try:
                work = ScheduledWork(id=task_id, name=names[task_id], start=start, finish=finish, duration_days=float(duration), resources=resources, contractor=contractor)
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            staged.append((order[task_id], work))
        # Unknown (synthetic) ids are skipped by design; only a frame with no
        # known works at all is a scheduling failure. Partial frames stay
        # tolerated to match the mirrored backend parser contract.
        if not staged:
            raise RuntimeError(E_SCHEDULING_FAILED) from None
        staged.sort(key=lambda item: item[0])
        return [work for _, work in staged]
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None

def schedule_project(request: ScheduleProjectRequest, *, environ: Mapping[str, str] | None = None, http_client: object | None = None, s3_client: object | None = None, storage_factory: Callable[..., Any] | None = None, model_class: Any | None = None, resource_model: str = DEFAULT_RESOURCE_MODEL, contractor_size: str = DEFAULT_CONTRACTOR_SIZE, project_name: str = DEFAULT_PROJECT_NAME, project_start_date: date | None = None, start_date_provider: Callable[[], date] | None = None, include_sampo_dump: bool = DEFAULT_INCLUDE_SAMPO_DUMP) -> ScheduleResult:
    """Run the full pipeline and return the schedule result.

    All five algorithms and three objectives flow through
    ``run_scheduling_pipeline``; works keep deterministic input order.

    Raises:
        PlanningValidationError: Safe ``E_*`` code for bad inputs/options.
        PlanningRuntimeError: ``E_INTERNAL``/``E_SCHEDULING_FAILED`` on failures.
    """
    try:
        if not isinstance(request, ScheduleProjectRequest):
            raise RuntimeError(E_INTERNAL) from None
        logger.info(
            "[PLANNING_MCP][ADAPTER] op=schedule_project algorithm=%s objective=%s -- start",
            request.algorithm,
            request.optimization_objective,
        )
        _validate_options(resource_model, contractor_size, project_name, include_sampo_dump)
        start_date = _resolve_start_date(project_start_date, start_date_provider)
        scaler = _resolve_scaler(contractor_size)
        started = time.perf_counter()
        rows = load_planning_rows(request.csv_url, environ=environ, http_client=http_client, s3_client=s3_client)
        storage = load_model_storage(environ=environ, factory=storage_factory)
        model = ResourceModelAdapter(storage, model_class)
        estimator = FieldDevWorkEstimator(resource_model=model, model_storage=storage, resource_model_type=resource_model)
        built = build_work_graph(rows, estimator)
        scheduled = run_scheduling_pipeline(built.work_graph, estimator, request, start_date, scaler)
        names = {row.activity_id: row.activity_name for row in rows}
        order = {row.activity_id: pos for pos, row in enumerate(rows)}
        start_datetime = datetime(start_date.year, start_date.month, start_date.day)
        works = _convert_works(scheduled, names, order, start_datetime)
        dump_dict: dict | None = None
        if include_sampo_dump:
            dump = getattr(scheduled, "dumpd", None)
            if not callable(dump):
                raise RuntimeError(E_INTERNAL) from None
            try:
                produced = dump()
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            if not isinstance(produced, dict):
                raise RuntimeError(E_INTERNAL) from None
            dump_dict = produced
        elapsed = float(time.perf_counter() - started)
        try:
            result = ScheduleResult(project_name=project_name, project_start_date=start_date, algorithm=request.algorithm, optimization_objective=request.optimization_objective, execution_time=elapsed, works=works, edges=list(built.edges), warnings=[], sampo_dump=dump_dict)
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
        logger.info(
            "[PLANNING_MCP][ADAPTER] op=schedule_project -- finished works=%d elapsed=%.1fs",
            len(works),
            elapsed,
        )
        return result
    except ValueError as exc:
        if _is_domain_error(exc):
            raise
        logger.error("[PLANNING_MCP][ADAPTER] op=schedule_project -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except RuntimeError as exc:
        if _is_domain_error(exc):
            raise
        logger.error("[PLANNING_MCP][ADAPTER] op=schedule_project -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except Exception as exc:
        logger.error("[PLANNING_MCP][ADAPTER] op=schedule_project -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
