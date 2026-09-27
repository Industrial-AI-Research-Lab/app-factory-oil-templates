"""Trusted read-only model storage boundary.

Wraps an injected ``stairs-storage.MschmAdapter`` and exposes only keyword-only
``get_res_model`` / ``get_perf_model`` reads against the trusted admin DB.
No save/remove/train/get_all surface, no blob decoding here (JSON first,
legacy pickle for trusted-DB blobs lives in ``stairs_resource_model``),
no value logging. DSN is read only at call time from ``RM_ADAPTER_CONN_STR``.
"""


import os
import atexit
from collections import OrderedDict
from threading import RLock
from typing import Any, Callable, Mapping

from .config import E_RESOURCE_MODEL_MISSING, RM_ADAPTER_CONN_STR_ENV_NAME
from stairs_storage import MschmAdapter

ModelRows = list[dict[str, object]]

__all__ = (
    "ResourceStorageError",
    "ReadOnlyModelStorage",
    "load_model_storage",
)

_instances: OrderedDict[str, MschmAdapter] = OrderedDict()
_instances_lock = RLock()


def _dispose_instances() -> None:
    """Close cached adapters at interpreter exit; best effort per adapter."""
    with _instances_lock:
        adapters = list(_instances.values())
        _instances.clear()
    for adapter in adapters:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()


atexit.register(_dispose_instances)


class ResourceStorageError(RuntimeError):
    """Safe storage failure without secrets or raw rows."""


def _require_non_blank(value: object, code: str = E_RESOURCE_MODEL_MISSING) -> str:
    """Validate non-blank string without trimming the stored value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code) from None
    return value


def _require_category(value: object) -> str:
    """Require category string, preserving source empty-string semantics."""
    if not isinstance(value, str):
        raise ValueError(E_RESOURCE_MODEL_MISSING) from None
    return value


def _require_optional_key(value: object) -> None:
    """Validate optional model/measurement key when provided."""
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(E_RESOURCE_MODEL_MISSING) from None


class ReadOnlyModelStorage:
    """Read-only view over an injected category-aware model adapter.

    Only ``get_res_model`` and ``get_perf_model`` are public. The wrapped
    adapter stays private; no save/remove/train/get_all/upload is exposed.
    """

    def __init__(self, adapter: MschmAdapter) -> None:
        """Store the source ``MschmAdapter`` without I/O."""
        if adapter is None:
            raise ValueError(E_RESOURCE_MODEL_MISSING) from None
        self._adapter: MschmAdapter = adapter

    def get_res_model(
        self,
        *,
        name: str,
        category: str,
        model_type: str | None = None,
        measurement_type: str | None = None,
    ) -> ModelRows:
        """Return a resource model row set for the trusted DB."""
        _require_non_blank(name)
        _require_category(category)
        _require_optional_key(model_type)
        _require_optional_key(measurement_type)
        try:
            return self._adapter.get_res_model(
                name=name,
                category=category,
                model_type=model_type,
                measurement_type=measurement_type,
            )
        except Exception:
            raise ResourceStorageError(E_RESOURCE_MODEL_MISSING) from None

    def get_perf_model(
        self,
        *,
        name: str,
        category: str,
        model_type: str | None = None,
        measurement_type: str | None = None,
    ) -> ModelRows:
        """Return a performance model row set for the trusted DB."""
        _require_non_blank(name)
        _require_category(category)
        _require_optional_key(model_type)
        _require_optional_key(measurement_type)
        try:
            return self._adapter.get_perf_model(
                name=name,
                category=category,
                model_type=model_type,
                measurement_type=measurement_type,
            )
        except Exception:
            raise ResourceStorageError(E_RESOURCE_MODEL_MISSING) from None


def load_model_storage(
    environ: Mapping[str, str] | None = None,
    factory: Callable[..., MschmAdapter] | None = None,
) -> ReadOnlyModelStorage:
    """Build a read-only storage from ``RM_ADAPTER_CONN_STR`` at call time.

    Reads only the DSN name, requires a non-blank value, never logs it,
    constructs the vendored ``stairs-storage`` adapter with ``url``.
    No import-time I/O.
    """
    source: Mapping[str, str] = environ if environ is not None else os.environ
    try:
        raw = source.get(RM_ADAPTER_CONN_STR_ENV_NAME)
    except Exception:
        raise ResourceStorageError(E_RESOURCE_MODEL_MISSING) from None
    if not isinstance(raw, str) or not raw.strip():
        raise ResourceStorageError(E_RESOURCE_MODEL_MISSING) from None
    try:
        if factory is not None:
            adapter = factory(url=raw)
        else:
            with _instances_lock:
                adapter = _instances.get(raw)
                if adapter is None:
                    adapter = MschmAdapter(url=raw)
                    _instances[raw] = adapter
                    if len(_instances) > 4:
                        _, expired = _instances.popitem(last=False)
                        expired.close()
                else:
                    _instances.move_to_end(raw)
    except ResourceStorageError:
        raise
    except Exception:
        raise ResourceStorageError(E_RESOURCE_MODEL_MISSING) from None
    return ReadOnlyModelStorage(adapter)
