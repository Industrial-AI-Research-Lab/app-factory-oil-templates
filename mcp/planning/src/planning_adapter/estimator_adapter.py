"""Standalone lazy FieldDev estimator for the Planning MCP.

Mirrors the backend ``FieldDev`` over injected resource/storage boundaries.
No import-time I/O, no environment reads, no DB clients, no backend imports.
"""


import ast
from itertools import chain
from operator import attrgetter
from random import Random
from typing import Any, Type

from sampo.schemas import WorkerReq, WorkEstimationMode, WorkerProductivityMode, WorkTimeEstimator, Worker, WorkUnit
from sampo.schemas.time import Time
from sampo.utilities.collections_util import build_index

from .config import E_RESOURCE_MODEL_MISSING, E_RESOURCE_REQUIREMENTS_INVALID

__all__ = (
    "ResourceModelMissingError",
    "ResourceModelRequirementsError",
    "FieldDevWorkEstimator",
    "SERVICE_WORKS",
    "DEFAULT_MODEL_NAME",
    "HISTORICAL_MODEL_NAME",
)

# Synthetic boundary works: no resources, zero duration, in both backend languages.
SERVICE_WORKS: tuple[str, ...] = (
    "Начало работ по марке",
    "Окончание работ по марке",
    "NaN",
    "start of project",
    "finish of project",
)

# Resource model names mirrored from the backend enums.
DEFAULT_MODEL_NAME = "standard"
HISTORICAL_MODEL_NAME = "historical"


class ResourceModelMissingError(RuntimeError):
    """Safe missing-model error without raw keys or secrets."""

    def __init__(self) -> None:
        """Carry only the E_RESOURCE_MODEL_MISSING code, no details."""
        super().__init__(E_RESOURCE_MODEL_MISSING)


class ResourceModelRequirementsError(RuntimeError):
    """Safe invalid-requirements error without raw values."""

    def __init__(self) -> None:
        """Carry only the E_RESOURCE_REQUIREMENTS_INVALID code, no values."""
        super().__init__(E_RESOURCE_REQUIREMENTS_INVALID)


def _coerce_model_name(model_name: Any) -> dict[str, Any]:
    """Parse dict|string model_name safely via ast.literal_eval."""
    if isinstance(model_name, str):
        try:
            parsed = ast.literal_eval(model_name)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return model_name if isinstance(model_name, dict) else {}


class FieldDevWorkEstimator(WorkTimeEstimator):
    """Lazy SAMPO estimator with injected resource/storage boundaries."""

    def __init__(
        self,
        resource_model: Any,
        model_storage: Any,
        resource_model_type: str = DEFAULT_MODEL_NAME,
        rand: Random | None = None,
    ) -> None:
        """Store injected model/storage without I/O; None deps are missing-model."""
        if resource_model is None or model_storage is None:
            raise ResourceModelMissingError() from None
        self._model = resource_model
        self._gp_adapter = model_storage
        self._resource_model_type = resource_model_type
        self._use_idle = True
        self._estimation_mode = WorkEstimationMode.Realistic
        self.rand = rand if rand is not None else Random()
        self._productivity_mode = WorkerProductivityMode.Static

    @staticmethod
    def _normalize_model_name(
        model_name: dict[str, Any] | str | None,
        fallback_name: str | None = None,
        fallback_measurement: str | None = None,
    ) -> dict[str, Any]:
        """Normalize source model_name with safe fallbacks."""
        parsed = _coerce_model_name(model_name)
        resolved_name = parsed.get("name") or parsed.get("granular_name") or fallback_name
        if resolved_name is None or str(resolved_name).strip() == "":
            raise ResourceModelMissingError() from None
        measurement_raw = parsed.get("measurement") or fallback_measurement
        return {
            "name": str(resolved_name),
            "category": "" if parsed.get("category") is None else str(parsed.get("category")),
            "measurement": "" if measurement_raw is None else str(measurement_raw),
        }

    def _mode_str(self) -> str:
        """Map source estimation mode to quantile string."""
        match self._estimation_mode:
            case WorkEstimationMode.Optimistic:
                return "0.1"
            case WorkEstimationMode.Realistic:
                return "0.5"
            case _:
                return "0.9"

    def _has_perf_model(self, name: str, category: str, measurement: str, model_type: str) -> bool:
        """Check storage for a perf model without leaking raw values."""
        try:
            found = self._gp_adapter.get_perf_model(
                name=name,
                category=category,
                model_type=model_type,
                measurement_type=measurement,
            )
        except ResourceModelMissingError:
            raise
        except ResourceModelRequirementsError:
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        return bool(found)

    def _resolve_model_type(self, category: str, name: str, measurement: str) -> str:
        """Apply source historical fallback to standard."""
        current = self._resource_model_type
        if current == HISTORICAL_MODEL_NAME and not self._has_perf_model(
            name, category, measurement, current
        ):
            current = DEFAULT_MODEL_NAME
            if not self._has_perf_model(name, category, measurement, current):
                raise ResourceModelMissingError() from None
        return current

    def find_work_resources(
        self,
        model_name: dict[str, Any],
        work_volume: float,
        resource_name: list[str] | None = None, # The parameter is used to save the interface.
    ) -> list[WorkerReq]:
        """Return worker requirements for a model name and volume."""
        try:
            normalized = self._normalize_model_name(model_name)
        except ResourceModelMissingError:
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        work_name = str(normalized["name"])
        if work_name in SERVICE_WORKS:
            return []
        try:
            current = self._resolve_model_type(
                str(normalized["category"]), work_name, str(normalized["measurement"])
            )
        except (ResourceModelMissingError, ResourceModelRequirementsError):
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        try:
            worker_req_dict = self._model.get_resources_volumes(
                category=str(normalized["category"]),
                work_name=work_name,
                work_volume=work_volume,
                measurement=str(normalized["measurement"]),
                model_type=current,
            )
        except ResourceModelMissingError:
            raise
        except ResourceModelRequirementsError:
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        try:
            worker_reqs: list[list[WorkerReq]] = []
            for worker_req_list in worker_req_dict.values():
                req_items: list[WorkerReq] = []
                for req in worker_req_list:
                    if req["max_count"] < req["min_count"]:
                        raise ResourceModelRequirementsError() from None
                    req_items.append(
                        WorkerReq(
                            kind=req["kind"],
                            volume=Time(req["volume"]),
                            min_count=req["min_count"],
                            max_count=req["max_count"],
                        )
                    )
                worker_reqs.append(req_items)
        except ResourceModelRequirementsError:
            raise
        except ResourceModelMissingError:
            raise
        except Exception:
            raise ResourceModelRequirementsError() from None
        return list(chain.from_iterable(worker_reqs))

    def estimate_time(self, work_unit: WorkUnit, worker_list: list[Worker]) -> Time:
        """Estimate duration for a work unit with assigned workers."""
        try:
            raw_name = getattr(work_unit, "model_name", None)
            fallback_name = getattr(work_unit, "name", None)
            fallback_measurement = getattr(work_unit, "volume_type", None)
            normalized = self._normalize_model_name(
                raw_name,
                fallback_name=fallback_name,
                fallback_measurement=fallback_measurement,
            )
            normalized["volume"] = getattr(work_unit, "volume")
            work_name = str(normalized["name"]).split("_stage_")[0]
            normalized["name"] = work_name
            items: list[Any] = list(worker_list) if worker_list is not None else []
            worker_payload = [{"name": w.name, "_count": w.count} for w in items]
            name2worker = build_index(items, attrgetter("name"))
            reqs = getattr(work_unit, "worker_reqs", []) or []
            for res_req in reqs:
                if name2worker.get(res_req.kind, None) is None:
                    worker_payload.append({"name": res_req.kind, "_count": 0})
        except (ResourceModelMissingError, ResourceModelRequirementsError):
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        if work_name in SERVICE_WORKS:
            return Time(0)
        try:
            current = self._resolve_model_type(
                str(normalized["category"]), work_name, str(normalized["measurement"])
            )
        except (ResourceModelMissingError, ResourceModelRequirementsError):
            raise
        except Exception:
            raise ResourceModelMissingError() from None
        try:
            raw = self._model.estimate_time(
                work_unit=normalized,
                worker_list=worker_payload,
                model_type=current,
                mode=self._mode_str(),
            )
            return Time(int(raw))
        except (ResourceModelMissingError, ResourceModelRequirementsError):
            raise
        except Exception:
            raise ResourceModelMissingError() from None

    def set_estimation_mode(
        self, use_idle: bool = True, mode: WorkEstimationMode = WorkEstimationMode.Realistic
    ) -> None:
        """Set source estimation mode without I/O."""
        self._use_idle = use_idle
        self._estimation_mode = mode

    def set_productivity_mode(
        self, mode: WorkerProductivityMode = WorkerProductivityMode.Static
    ) -> None:
        """Set source productivity mode without I/O."""
        self._productivity_mode = mode

    def get_recreate_info(self) -> tuple[Type, tuple]:
        """Return recreate info with injected deps, without any DSN."""
        return FieldDevWorkEstimator, (self._model, self._gp_adapter, self._resource_model_type, self.rand)

    def get_model_name_keys(self) -> list[str]:
        """Return source model name keys."""
        return ["name", "measurement"]
