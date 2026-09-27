"""Estimator historical fallback and service-work shortcuts (no SAMPO execution)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planning_adapter.estimator_adapter import (  # noqa: E402
    FieldDevWorkEstimator,
    ResourceModelMissingError,
)


class _Storage:
    """Fake model storage with controllable perf-model presence."""

    def __init__(self, perf: set):
        self._perf = set(perf)
        self.seen: list = []

    def get_perf_model(self, *args, **kwargs):
        if args:
            name, _category, _measurement, model_type = args
        else:
            name, model_type = kwargs["name"], kwargs["model_type"]
        self.seen.append(model_type)
        return [{"data": b"{}"}] if (name, model_type) in self._perf else []

    def get_res_model(self, **kw):
        raise AssertionError("not used here")


class _Model:
    def __init__(self):
        self.seen: list = []

    def get_resources_volumes(self, *, category, work_name, work_volume, measurement, model_type):
        self.seen.append(model_type)
        return {"worker_reqs": [{"kind": "k", "volume": 1, "min_count": 1, "max_count": 1}]}

    def estimate_time(self, **kw):
        raise AssertionError("not used here")


def _est(work: str, perf: set, resource_model_type: str = "historical"):
    model, storage = _Model(), _Storage(perf)
    est = FieldDevWorkEstimator(resource_model=model, model_storage=storage, resource_model_type=resource_model_type)
    return est, model, storage


def test_historical_falls_back_to_standard():
    est, model, storage = _est("w", perf={("w", "standard")})
    est.find_work_resources({"name": "w", "category": "", "measurement": "pcs"}, 1.0)
    assert storage.seen == ["historical", "standard"]
    assert model.seen == ["standard"]


def test_historical_missing_everywhere_fails():
    est, _, _ = _est("w", perf=set())
    # No perf rows at all: _resolve_model_type raises before touching resources.
    est._gp_adapter._perf = {("other", "standard")}
    with pytest.raises(ResourceModelMissingError):
        est.find_work_resources({"name": "w", "category": "", "measurement": "pcs"}, 1.0)


def test_historical_present_no_fallback():
    est, model, storage = _est("w", perf={("w", "historical")})
    est.find_work_resources({"name": "w", "category": "", "measurement": "pcs"}, 1.0)
    assert storage.seen == ["historical"]
    assert model.seen == ["historical"]


def test_service_works_short_circuit():
    est, model, _ = _est("w", perf=set(), resource_model_type="standard")
    assert est.find_work_resources({"name": "start of project", "category": "", "measurement": "pcs"}, 5.0) == []
    assert model.seen == []
