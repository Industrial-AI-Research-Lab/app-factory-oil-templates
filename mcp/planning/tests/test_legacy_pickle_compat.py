"""Dual-format model compat: JSON planning-model/v1 plus legacy backend pickle.

The current external build populates the trusted model DB with pickle blobs;
new seeds use declarative JSON. Both must schedule successfully.
No DB, SAMPO, or network; stdlib pickle of module-level fakes only.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stairs_resource_model.res_time_model import ResTimeModel  # noqa: E402


class _LegacyResModel:
    """Picklable stand-in for a backend res estimator (predict triple)."""

    resources = ["carpenter"]

    def predict(self, work_volume: float = 0):
        del work_volume
        return [[2], [10], [4]]


class _LegacyTimeModel:
    """Picklable stand-in for a backend time estimator."""

    resources = ["carpenter"]

    def predict(self, volume: float, res_volumes: list):
        del volume, res_volumes
        return [3, 5, 9]


# The restricted unpickler only resolves estimator namespaces. Test fakes
# are exposed under an allowed model-namespace alias (test process only;
# never shipped in the image).
_TEST_MODELS_MODULE = "stairs_resource_model.models.legacy_test_support"
_test_models = sys.modules.get(_TEST_MODELS_MODULE)
if _test_models is None:
    import types

    _test_models = types.ModuleType(_TEST_MODELS_MODULE)
    sys.modules[_TEST_MODELS_MODULE] = _test_models
_LegacyResModel = type("LegacyResModel", (_LegacyResModel,), {"__module__": _TEST_MODELS_MODULE})
_LegacyTimeModel = type("LegacyTimeModel", (_LegacyTimeModel,), {"__module__": _TEST_MODELS_MODULE})
_test_models.LegacyResModel = _LegacyResModel
_test_models.LegacyTimeModel = _LegacyTimeModel


class _Wrapper:
    """Fake storage returning one blob for res/perf reads."""

    def __init__(self, res_blob: bytes, perf_blob: bytes) -> None:
        self._blobs = {"res": res_blob, "perf": perf_blob}

    def get_res_model(self, **_: object) -> list[dict[str, object]]:
        return [{"data": self._blobs["res"]}]

    def get_perf_model(self, **_: object) -> list[dict[str, object]]:
        return [{"data": self._blobs["perf"]}]


class _Empty:
    def get_res_model(self, **_: object) -> list:
        return []

    def get_perf_model(self, **_: object) -> list:
        return []


def _json_blobs() -> tuple[bytes, bytes]:
    import json

    res = json.dumps(
        {"schema": "planning-model/v1", "resources": [{"name": "carpenter", "counts": [2, 10, 20]}]}
    ).encode()
    perf = json.dumps({"schema": "planning-model/v1", "days_per_unit": [1.0, 2.0, 4.0]}).encode()
    return res, perf


def test_json_blobs_keep_working() -> None:
    model = ResTimeModel(_Wrapper(*_json_blobs()))
    got = model.get_resources_volumes(work_name="w", work_volume=3.0, measurement="pcs")
    assert got == {"worker_reqs": [{"kind": "carpenter", "volume": 30, "min_count": 2, "max_count": 20}]}
    assert model.estimate_time({"name": "w", "volume": 10.0, "measurement": "pcs"}, [{"name": "carpenter", "_count": 5}]) == 4


def test_pickle_blobs_from_current_build_work() -> None:
    res = pickle.dumps(_LegacyResModel())
    perf = pickle.dumps(_LegacyTimeModel())
    model = ResTimeModel(_Wrapper(res, perf))
    got = model.get_resources_volumes(work_name="w", work_volume=2.0, measurement="pcs")
    assert got == {"worker_reqs": [{"kind": "carpenter", "volume": 10, "min_count": 2, "max_count": 4}]}
    assert model.estimate_time({"name": "w", "volume": 7.0, "measurement": "pcs"}, [{"name": "carpenter", "_count": 2}]) == 5
    # Legacy zero semantics preserved on both paths.
    assert model.get_resources_volumes(work_name="w", work_volume=0, measurement="pcs") == {
        "worker_reqs": [{"kind": "carpenter", "volume": 0, "min_count": 0, "max_count": 0}]
    }
    assert model.estimate_time({"name": "w", "volume": 0, "measurement": "pcs"}, [{"name": "carpenter", "_count": 2}]) == 0
    assert model.estimate_time({"name": "w", "volume": 7.0, "measurement": "pcs"}, []) == 0


def test_malicious_pickle_blobs_fail_safe() -> None:
    evil_system = pickle.dumps(__import__("os").system)
    evil_eval = b"ceval\n(S'1+1'\ntR."
    for bad in (evil_system, evil_eval, b"cos\nsystem\n(S'echo pwned'\ntR."):
        model = ResTimeModel(_Wrapper(bad, bad))
        with pytest.raises(ValueError, match="invalid model"):
            model.get_resources_volumes(work_name="w", work_volume=1.0, measurement="pcs")
        with pytest.raises(ValueError, match="invalid model"):
            model.estimate_time({"name": "w", "volume": 1.0, "measurement": "pcs"}, [{"name": "c", "_count": 1}])


def test_missing_and_garbage_blobs_fail_safe() -> None:
    model = ResTimeModel(_Empty())
    with pytest.raises(ValueError, match="missing model"):
        model.get_resources_volumes(work_name="w", work_volume=1.0, measurement="pcs")
    bad = ResTimeModel(_Wrapper(b"\x00\x01not-a-model", b"\x00\x01not-a-model"))
    with pytest.raises(ValueError, match="invalid model"):
        bad.get_resources_volumes(work_name="w", work_volume=1.0, measurement="pcs")
    with pytest.raises(ValueError, match="invalid model"):
        bad.estimate_time({"name": "w", "volume": 1.0, "measurement": "pcs"}, [{"name": "c", "_count": 1}])
    big = ResTimeModel(_Wrapper(b"x" * 1_000_001, b"x" * 1_000_001))
    with pytest.raises(ValueError, match="invalid model"):
        big.get_resources_volumes(work_name="w", work_volume=1.0, measurement="pcs")
