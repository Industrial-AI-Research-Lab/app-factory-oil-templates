"""Model evaluator for Planning MCP: declarative JSON first, legacy pickle fallback.

Primary format is the non-executable declarative ``planning-model/v1`` JSON.
Blobs that are not JSON are retried as legacy pickle estimators written by
the backend training pipeline into the same trusted admin model DB
(``RM_ADAPTER_CONN_STR``). Pickle deserialization is confined to this module
and only runs for blobs that failed JSON decoding; the DB is assumed trusted
and admin-controlled (read-only access, no user-supplied bytes).
"""

import io
import json
import math
import pickle
from typing import Any

_SCHEMA = "planning-model/v1"
_MAX_MODEL_BYTES = 1_000_000

# Exact (module, name) globals allowed in legacy pickle blobs.
_ALLOWED_GLOBALS = frozenset(
    {
        ("builtins", "list"), ("builtins", "dict"), ("builtins", "tuple"),
        ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "str"),
        ("builtins", "int"), ("builtins", "float"), ("builtins", "bool"),
        ("builtins", "complex"), ("builtins", "bytes"), ("builtins", "bytearray"),
        ("builtins", "range"), ("builtins", "slice"),
        ("collections", "OrderedDict"), ("collections", "defaultdict"),
        ("collections", "deque"), ("collections", "Counter"),
        ("datetime", "datetime"), ("datetime", "date"),
        ("copyreg", "_reconstructor"),
    }
)

# Module prefixes allowed in legacy pickle blobs: estimator libraries plus
# the backend model classes. Anything else (os, sys, subprocess, backend
# runtime like agent_wrapper/jobs) is rejected.
_ALLOWED_MODULE_PREFIXES = (
    "numpy.",
    "sklearn.",
    "xgboost.",
    "scipy.",
    "joblib.",
    "stairs_resource_model.models.",
)


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler limited to estimator classes and safe containers."""

    def find_class(self, module: str, name: str) -> object:
        """Resolve only allowlisted globals; refuse everything else."""
        if (module, name) in _ALLOWED_GLOBALS or module.startswith(_ALLOWED_MODULE_PREFIXES):
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"forbidden class {module}.{name}")


def _number(value: object, *, non_negative: bool = True) -> float:
    """Coerce to a finite float, rejecting bools (and negatives by default)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid model")
    result = float(value)
    if not math.isfinite(result) or (non_negative and result < 0):
        raise ValueError("invalid model")
    return result


def _raw_bytes(value: object) -> bytes:
    """Return blob bytes with a DoS cap, shared by both decoders."""
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        raise ValueError("invalid model")
    if len(raw) > _MAX_MODEL_BYTES:
        raise ValueError("invalid model")
    return raw


def _decode_json(value: object) -> dict[str, Any]:
    """Decode a planning-model/v1 JSON blob or dict, else invalid model."""
    if isinstance(value, dict):
        data = value
    else:
        raw = _raw_bytes(value)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid model") from None
    if not isinstance(data, dict) or data.get("schema") != _SCHEMA:
        raise ValueError("invalid model")
    return data


def _decode_legacy(value: object) -> Any:
    """Unpickle a legacy backend estimator (trusted admin DB only).

    Deserialization is restricted to estimator classes and safe containers
    via :class:`_RestrictedUnpickler`; anything else fails as invalid model.
    """
    raw = _raw_bytes(value)
    try:
        model = _RestrictedUnpickler(io.BytesIO(raw)).load()
    except Exception:
        raise ValueError("invalid model") from None
    if not hasattr(model, "predict") or not hasattr(model, "resources"):
        raise ValueError("invalid model")
    return model


def _quantiles(values: object) -> tuple[float, float, float]:
    """Validate a 3-item list and return it sorted as (low, mid, high)."""
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("invalid model")
    normalized = sorted(_number(item) for item in values)
    return normalized[0], normalized[1], normalized[2]


def _legacy_worker_pairs(worker_list: object) -> list[tuple[str, float]]:
    """Normalize SAMPO-style (``.name``/``.count``) or dict workers to pairs."""
    if not isinstance(worker_list, (list, tuple)):
        raise ValueError("invalid model")
    pairs: list[tuple[str, float]] = []
    for worker in worker_list:
        if isinstance(worker, dict):
            name, count = worker.get("name"), worker.get("_count")
        else:
            name, count = getattr(worker, "name", None), getattr(worker, "count", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid model")
        pairs.append((name, _number(count)))
    return pairs


def _legacy_resources_volumes(model: Any, work_volume: float) -> dict[str, list[dict[str, object]]]:
    """Original backend semantics: zero volume yields all-zero requirements."""
    volume = _number(work_volume)
    resources = getattr(model, "resources", None)
    if not isinstance(resources, (list, tuple)) or not resources:
        raise ValueError("invalid model")
    if volume == 0:
        return {"worker_reqs": [{"kind": name, "volume": 0, "min_count": 0, "max_count": 0} for name in resources]}
    try:
        res_volumes = model.predict(work_volume=volume)
    except Exception:
        raise ValueError("invalid model") from None
    try:
        worker_reqs = [
            {
                "kind": resources[i],
                "volume": int(res_volumes[1][i]),
                "min_count": int(res_volumes[0][i]),
                "max_count": int(res_volumes[2][i]),
            }
            for i in range(len(resources))
        ]
    except Exception:
        raise ValueError("invalid model") from None
    return {"worker_reqs": worker_reqs}


def _legacy_estimate_time(model: Any, work_unit: dict[str, object], worker_list: object, mode: str) -> int:
    """Original backend semantics: ``predict`` triple indexed by quantile mode."""
    pairs = _legacy_worker_pairs(worker_list)
    if not pairs:
        return 0
    volume = _number(work_unit.get("volume"))
    if volume == 0:
        return 0
    res_dict = dict(pairs)
    try:
        resources = getattr(model, "resources", None)
        res_volumes = [res_dict[name] for name in resources]
    except (KeyError, TypeError):
        raise ValueError("missing model") from None
    try:
        times = model.predict(volume, res_volumes)
    except Exception:
        raise ValueError("invalid model") from None
    try:
        if mode == "0.1":
            return int(times[0])
        if mode == "0.5":
            return int(times[1])
        if mode == "0.9":
            return int(times[2])
    except Exception:
        raise ValueError("invalid model") from None
    raise ValueError("invalid quantile")


class ResTimeModel:
    """Evaluate declarative JSON models, falling back to legacy pickle blobs."""

    def __init__(self, dbwrapper: object) -> None:
        """Bind a storage wrapper exposing get_res_model/get_perf_model."""
        self.wrapper = dbwrapper

    def get_resources_volumes(self, work_name: str, work_volume: float, measurement: str, model_type: str | None = None, shift: float = 11.0) -> dict[str, list[dict[str, object]]]:
        """Return worker requirements, trying JSON first, legacy pickle second."""
        rows = self.wrapper.get_res_model(name=work_name, model_type=model_type, measurement_type=measurement)
        if not rows:
            raise ValueError("missing model")
        blob = rows[0].get("data")
        try:
            data = _decode_json(blob)
        except ValueError:
            return _legacy_resources_volumes(_decode_legacy(blob), work_volume)
        resources = data.get("resources")
        if not isinstance(resources, list) or not resources:
            raise ValueError("invalid model")
        result = []
        for resource in resources:
            if not isinstance(resource, dict) or not isinstance(resource.get("name"), str) or not resource["name"].strip():
                raise ValueError("invalid model")
            low, mid, high = _quantiles(resource.get("counts"))
            scale = _number(work_volume)
            result.append({"kind": resource["name"], "volume": math.ceil(mid * scale), "min_count": math.ceil(low), "max_count": math.ceil(high)})
        return {"worker_reqs": result}

    def estimate_time(self, work_unit: dict[str, object], worker_list: list[dict[str, object]], model_type: str | None = None, mode: str = "0.5") -> int:
        """Return duration in whole units, trying JSON first, legacy pickle second."""
        if not worker_list or _number(work_unit.get("volume")) == 0:
            return 0
        rows = self.wrapper.get_perf_model(name=work_unit["name"], model_type=model_type, measurement_type=work_unit["measurement"])
        if not rows:
            raise ValueError("missing model")
        blob = rows[0].get("data")
        try:
            data = _decode_json(blob)
        except ValueError:
            return _legacy_estimate_time(_decode_legacy(blob), work_unit, worker_list, mode)
        low, mid, high = _quantiles(data.get("days_per_unit"))
        factor = {"0.1": low, "0.5": mid, "0.9": high}.get(mode)
        if factor is None:
            raise ValueError("invalid quantile")
        count = sum(_number(worker.get("_count")) for worker in worker_list)
        if count <= 0:
            return 0
        duration = math.ceil(_number(work_unit.get("volume")) * factor / count)
        if duration < 0 or not math.isfinite(duration):
            raise ValueError("invalid model")
        return duration
