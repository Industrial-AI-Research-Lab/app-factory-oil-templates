"""Category-compatible ResTimeModel bridge

Bind ``category`` for model reads; the vendored core does not accept it.

No model bytes in, no uploads out, no backend runtime imports.
"""

import math
from collections.abc import Sequence
from typing import Any

from .config import (
    ALLOWED_RESOURCE_MODELS,
    E_INTERNAL,
    E_MEASUREMENT_MISSING,
    E_RESOURCE_MODEL_MISSING,
    E_RESOURCE_REQUIREMENTS_INVALID,
)
from .storage_adapter import ReadOnlyModelStorage
from stairs_resource_model.res_time_model import ResTimeModel
from stairs_resource_model.schema import ResourceDict, WorkerReqs, WorkUnit

__all__ = (
    "ResourceModelError",
    "ResourceModelAdapter",
)

# Duration quantile: optimistic 0.1, median 0.5 (default), pessimistic 0.9.
_VALID_MODES = frozenset({"0.5", "0.1", "0.9"})


class ResourceModelError(RuntimeError):
    """Safe resource-model failure without secrets or raw rows."""


def _require_non_blank(value: object, code: str) -> str:
    """Validate non-blank string, preserving the original value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code) from None
    return value


def _require_category(value: object) -> str:
    """Require category string, preserving source empty-string semantics."""
    if not isinstance(value, str):
        raise ValueError(E_RESOURCE_MODEL_MISSING) from None
    return value


def _require_model_type(value: object) -> None:
    """Validate optional model kind against the canonical set."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(E_RESOURCE_MODEL_MISSING) from None
    if value not in ALLOWED_RESOURCE_MODELS:
        raise ValueError(E_RESOURCE_MODEL_MISSING) from None


def _require_volume(value: object) -> float:
    """Validate finite non-negative volume, preserving zero-volume."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(E_RESOURCE_REQUIREMENTS_INVALID) from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(E_RESOURCE_REQUIREMENTS_INVALID) from None
    return value


class _CategoryBoundView:
    """Private view injecting a bound category for the planning core."""

    def __init__(self, storage: ReadOnlyModelStorage, category: str) -> None:
        """Bind storage and category without I/O."""
        self._storage = storage
        self._category = category

    def get_res_model(
        self,
        name: str,
        model_type: str | None = None,
        measurement_type: str | None = None,
        **_: Any,
    ) -> list[dict[str, object]]:
        """Delegate with the bound category injected."""
        return self._storage.get_res_model(
            name=name,
            category=self._category,
            model_type=model_type,
            measurement_type=measurement_type,
        )

    def get_perf_model(
        self,
        name: str,
        model_type: str | None = None,
        measurement_type: str | None = None,
        **_: Any,
    ) -> list[dict[str, object]]:
        """Delegate with the bound category injected."""
        return self._storage.get_perf_model(
            name=name,
            category=self._category,
            model_type=model_type,
            measurement_type=measurement_type,
        )


class ResourceModelAdapter:
    """Category-aware facade over the vendored ResTimeModel.

    Public methods accept ``category`` explicitly, bind it via
    ``_CategoryBoundView``, then instantiate and use ``ResTimeModel``.
    Source zero-volume (zeros) and no-worker (0) behavior is preserved by
    direct delegation. Constructor injection is retained for tests and
    controlled deployments.
    """

    def __init__(
        self,
        storage: ReadOnlyModelStorage,
        model_class: type[ResTimeModel] | None = None,
    ) -> None:
        """Store injected storage and optional core class without I/O."""
        if storage is None:
            raise ResourceModelError(E_RESOURCE_MODEL_MISSING) from None
        self._storage = storage
        self._model_class = model_class

    def _core_class(self) -> type[ResTimeModel]:
        """Return the injected or vendored ResTimeModel class."""
        return self._model_class or ResTimeModel

    def _new_core(self, category: str) -> ResTimeModel:
        """Instantiate ResTimeModel over the category-bound core view."""
        try:
            view = _CategoryBoundView(self._storage, category)
            return self._core_class()(view)
        except ResourceModelError:
            raise
        except Exception:
            raise ResourceModelError(E_RESOURCE_MODEL_MISSING) from None

    def get_resources_volumes(
        self,
        *,
        category: str,
        work_name: str,
        work_volume: float,
        measurement: str,
        model_type: str | None = None,
    ) -> WorkerReqs:
        """Return worker requirements for a named work, category injected."""
        try:
            _require_non_blank(work_name, E_RESOURCE_MODEL_MISSING)
            _require_category(category)
            _require_non_blank(measurement, E_MEASUREMENT_MISSING)
            _require_model_type(model_type)
            _require_volume(work_volume)
            core_model = self._new_core(category)
            try:
                result = core_model.get_resources_volumes(
                    work_name=work_name,
                    work_volume=work_volume,
                    measurement=measurement,
                    model_type=model_type,
                )
            except Exception:
                raise ResourceModelError(E_RESOURCE_MODEL_MISSING) from None
            if result is None:
                raise ResourceModelError(E_RESOURCE_MODEL_MISSING) from None
            if not isinstance(result, dict):
                raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
            for requirements in result.values():
                if not isinstance(requirements, list):
                    raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
                for requirement in requirements:
                    if not isinstance(requirement, dict):
                        raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
                    values = (requirement.get("volume"), requirement.get("min_count"), requirement.get("max_count"))
                    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in values):
                        raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
                    if requirement["min_count"] > requirement["max_count"]:
                        raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
            return result
        except ValueError:
            raise
        except ResourceModelError:
            raise
        except Exception:
            raise ResourceModelError(E_INTERNAL) from None

    def estimate_time(
        self,
        *,
        work_unit: dict[str, object],
        worker_list: Sequence[ResourceDict] | None,
        model_type: str | None = None,
        mode: str = "0.5",
    ) -> int:
        """Return duration with category injected, preserving zeros.
        """
        try:
            if not isinstance(work_unit, dict):
                raise ValueError(E_RESOURCE_MODEL_MISSING) from None
            raw_name = _require_non_blank(
                work_unit.get("name"), E_RESOURCE_MODEL_MISSING
            )
            raw_category = _require_category(work_unit.get("category"))
            raw_measurement = _require_non_blank(
                work_unit.get("measurement"), E_MEASUREMENT_MISSING
            )
            raw_volume = _require_volume(work_unit.get("volume"))
            _require_model_type(model_type)
            if not isinstance(mode, str) or mode not in _VALID_MODES:
                raise ValueError(E_RESOURCE_REQUIREMENTS_INVALID) from None
            if worker_list is None:
                resolved_workers: list[ResourceDict] = []
            elif isinstance(worker_list, (list, tuple)):
                resolved_workers = list(worker_list)
            else:
                raise ValueError(E_RESOURCE_REQUIREMENTS_INVALID) from None
            category = raw_category
            core_model = self._new_core(category)
            core_unit: WorkUnit = {
                "name": raw_name,
                "volume": raw_volume,
                "measurement": raw_measurement,
            }
            try:
                result = core_model.estimate_time(
                    work_unit=core_unit,
                    worker_list=resolved_workers,
                    model_type=model_type,
                    mode=mode,
                )
                if isinstance(result, bool) or not isinstance(result, (int, float)):
                    raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
                if not math.isfinite(result) or result < 0:
                    raise ResourceModelError(E_RESOURCE_REQUIREMENTS_INVALID) from None
                return int(math.ceil(result))
            except Exception:
                raise ResourceModelError(E_RESOURCE_MODEL_MISSING) from None
        except ValueError:
            raise
        except ResourceModelError:
            raise
        except Exception:
            raise ResourceModelError(E_INTERNAL) from None
