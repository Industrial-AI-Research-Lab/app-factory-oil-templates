"""Service pipeline contracts for the Planning MCP

Verifies exact adapter order, dependency injection, algorithm and
objective propagation, deterministic work and resource ordering,
synthetic-node filtering, safe exception mapping with redaction, and
optional SAMPO dump behavior. No real network, S3, DB, or SAMPO runs.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import planning_adapter.service as svc  # noqa: E402
from planning_adapter.config import E_INTERNAL, E_OPTIONS_INVALID, E_SCHEDULING_FAILED  # noqa: E402
from planning_adapter.models import PlanningRow, ScheduleProjectRequest  # noqa: E402

START = date(2026, 1, 5)
URL = "https://example.invalid/data.csv"


def req(**kw) -> ScheduleProjectRequest:
    """Build a valid request with safe defaults."""
    base = {"csv_url": URL, "algorithm": "heft", "optimization_objective": "min_time"}
    base.update(kw)
    return ScheduleProjectRequest(**base)  # type: ignore[arg-type]


def rows2():
    """Return two deterministic input rows."""
    return [
        PlanningRow(activity_id="A1", activity_name="Alpha", volume=1.0, measurement="pcs"),
        PlanningRow(activity_id="B1", activity_name="Beta", volume=2.0, measurement="pcs"),
    ]


def frame_row(tid: str, start: datetime, finish: datetime, workers, contractor="C1"):
    """Build one fake source dataframe row."""
    return SimpleNamespace(task_id=tid, start=start, finish=finish, workers=workers, contractor=contractor)


class FakeFrame:
    """Minimal frame exposing itertuples like the source schedule."""

    def __init__(self, items):
        self._items = list(items)

    def itertuples(self, index=False):  # noqa: ANN001, ANN202
        return list(self._items)


def scheduled_with(items, dump=None, has_dump=True):
    """Build a fake ScheduledProject with optional dumpd."""
    sched = SimpleNamespace(merged_stages_datetime_df=lambda _dt: FakeFrame(items))
    obj = SimpleNamespace(schedule=sched)
    if has_dump:
        obj.dumpd = lambda: dump
    return obj


def install(monkeypatch, *, items, dump=None, has_dump=True, fail_at=None, fail_exc=None):
    """Monkeypatch module adapters; record exact pipeline order."""
    calls: list[str] = []
    seen: dict = {}
    made_rows = rows2()
    storage = SimpleNamespace(tag="storage")
    built = SimpleNamespace(work_graph=SimpleNamespace(tag="wg"), edges=())

    def fake_load(csv_url, *, environ=None, http_client=None, s3_client=None):
        calls.append("load")
        seen["load"] = (csv_url, environ, http_client, s3_client)
        if fail_at == "load":
            raise fail_exc
        return made_rows

    def fake_storage_load(*, environ=None, factory=None):
        calls.append("storage")
        seen["storage"] = (environ, factory)
        if fail_at == "storage":
            raise fail_exc
        return storage

    class FakeAdapter:
        def __init__(self, st, cls=None):
            calls.append("adapter")
            seen["adapter"] = (st, cls)

    class FakeEstimator:
        def __init__(self, *, resource_model, model_storage, resource_model_type):
            calls.append("estimator")
            seen["estimator"] = (resource_model, model_storage, resource_model_type)

    def fake_build(r, e):
        calls.append("graph")
        seen["build"] = (r, e)
        if fail_at == "graph":
            raise fail_exc
        return built

    def fake_run(wg, est, request, start_date, scaler):
        calls.append("scheduler")
        seen["run"] = (wg, est, request, start_date, scaler)
        if fail_at == "scheduler":
            raise fail_exc
        return scheduled_with(items, dump=dump, has_dump=has_dump)

    monkeypatch.setattr(svc, "load_planning_rows", fake_load)
    monkeypatch.setattr(svc, "load_model_storage", fake_storage_load)
    monkeypatch.setattr(svc, "ResourceModelAdapter", FakeAdapter)
    monkeypatch.setattr(svc, "FieldDevWorkEstimator", FakeEstimator)
    monkeypatch.setattr(svc, "build_work_graph", fake_build)
    monkeypatch.setattr(svc, "run_scheduling_pipeline", fake_run)
    return calls, seen, made_rows


def test_pipeline_order_and_injection(monkeypatch) -> None:
    """Adapters run in load/storage/adapter/estimator/graph/scheduler order."""
    items = [frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"a": 1}),
             frame_row("B1", datetime(2026, 1, 6), datetime(2026, 1, 7), {"b": 1})]
    env = {"K": "v"}
    http, s3 = object(), object()
    factory = lambda **kw: SimpleNamespace()  # noqa: E731
    model_cls = type("M", (), {})
    calls, seen, _ = install(monkeypatch, items=items)
    out = svc.schedule_project(req(), environ=env, http_client=http, s3_client=s3,
                               storage_factory=factory, model_class=model_cls,
                               project_start_date=START)
    assert calls == ["load", "storage", "adapter", "estimator", "graph", "scheduler"]
    assert seen["load"] == (URL, env, http, s3)
    assert seen["storage"] == (env, factory)
    assert seen["adapter"][1] is model_cls
    assert seen["estimator"][2] == "standard"
    assert out.project_start_date == START and out.sampo_dump is None


def test_propagates_algorithm_objective_generations_deadline(monkeypatch) -> None:
    """Genetic request fields reach the scheduler unchanged."""
    items = [frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"a": 1}),
             frame_row("B1", datetime(2026, 1, 6), datetime(2026, 1, 8), {"b": 1})]
    calls, seen, _ = install(monkeypatch, items=items)
    request = req(algorithm="genetic", optimization_objective="deadline",
                  genetic_generations=7, deadline=date(2026, 2, 1))
    out = svc.schedule_project(request, project_start_date=START, contractor_size="max")
    assert calls[-1] == "scheduler"
    assert seen["run"][2] is request
    assert seen["run"][3] == START and seen["run"][4] == 10
    assert out.algorithm == "genetic" and out.optimization_objective == "deadline"


def test_deterministic_work_and_resource_ordering(monkeypatch) -> None:
    """Works follow input order; resources sort by name; no raw leak."""
    # Frame is reversed relative to input; workers dict is unsorted.
    items = [frame_row("B1", datetime(2026, 1, 6), datetime(2026, 1, 7), {"z": 1, "a": 2}),
             frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"m": 1})]
    install(monkeypatch, items=items)
    out = svc.schedule_project(req(), project_start_date=START)
    assert [w.id for w in out.works] == ["A1", "B1"]
    assert [r.name for r in out.works[1].resources] == ["a", "z"]
    assert out.edges == []


def test_synthetic_nodes_filtered(monkeypatch) -> None:
    """Unknown task ids are skipped; all-synthetic fails safely."""
    items = [frame_row("SYNTH", datetime(2026, 1, 5), datetime(2026, 1, 6), {"a": 1}),
             frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"a": 1})]
    install(monkeypatch, items=items)
    out = svc.schedule_project(req(), project_start_date=START)
    assert [w.id for w in out.works] == ["A1"]
    install(monkeypatch, items=[frame_row("GHOST", datetime(2026, 1, 5), datetime(2026, 1, 6), {})])
    with pytest.raises(RuntimeError) as ctx:
        svc.schedule_project(req(), project_start_date=START)
    assert str(ctx.value) == E_SCHEDULING_FAILED


def test_safe_exception_mapping_and_redaction(monkeypatch) -> None:
    """Known codes pass through; secrets map to E_INTERNAL without leak."""
    secret = "SECRET-TOKEN-xyz"
    install(monkeypatch, items=[], fail_at="load",
            fail_exc=RuntimeError("E_SCHEDULING_FAILED"))
    with pytest.raises(RuntimeError) as ctx:
        svc.schedule_project(req(), project_start_date=START)
    assert str(ctx.value) == E_SCHEDULING_FAILED
    for exc in (ValueError(f"oops {secret}"), RuntimeError(f"boom {secret}"), OSError("disk gone")):
        install(monkeypatch, items=[], fail_at="scheduler", fail_exc=exc)
        with pytest.raises(RuntimeError) as ctx2:
            svc.schedule_project(req(), project_start_date=START)
        assert str(ctx2.value) == E_INTERNAL
        assert secret not in str(ctx2.value)
        assert ctx2.value.__cause__ is None


def test_dump_behavior(monkeypatch) -> None:
    """Dump is None by default; dict stored when requested; bad dump fails."""
    items = [frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {})]
    install(monkeypatch, items=items, dump={"k": 1}, has_dump=True)
    assert svc.schedule_project(req(), project_start_date=START).sampo_dump is None
    install(monkeypatch, items=items, dump={"k": 1}, has_dump=True)
    out = svc.schedule_project(req(), project_start_date=START, include_sampo_dump=True)
    assert out.sampo_dump == {"k": 1}
    install(monkeypatch, items=items, has_dump=False)
    with pytest.raises(RuntimeError) as ctx:
        svc.schedule_project(req(), project_start_date=START, include_sampo_dump=True)
    assert str(ctx.value) == E_INTERNAL
    install(monkeypatch, items=items, dump=["not-a-dict"], has_dump=True)
    with pytest.raises(RuntimeError) as ctx2:
        svc.schedule_project(req(), project_start_date=START, include_sampo_dump=True)
    assert str(ctx2.value) == E_INTERNAL


def test_invalid_options_rejected(monkeypatch) -> None:
    """Bad resource model, contractor size, or dump flag fails fast."""
    items = [frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {})]
    install(monkeypatch, items=items)
    with pytest.raises(ValueError) as ctx:
        svc.schedule_project(req(), project_start_date=START, resource_model="nope")
    assert str(ctx.value).split(":")[0] == E_OPTIONS_INVALID
    with pytest.raises(ValueError) as ctx2:
        svc.schedule_project(req(), project_start_date=START, contractor_size="huge")
    assert str(ctx2.value).split(":")[0] == E_OPTIONS_INVALID
    with pytest.raises(ValueError) as ctx3:
        svc.schedule_project(req(), project_start_date=START, include_sampo_dump="yes")  # type: ignore[arg-type]
    assert str(ctx3.value).split(":")[0] == E_OPTIONS_INVALID


def test_convert_duplicate_task_id_fails(monkeypatch):
    install(
        monkeypatch,
        items=[
            frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"c": 1}),
            frame_row("A1", datetime(2026, 1, 6), datetime(2026, 1, 7), {"c": 1}),
        ],
    )
    with pytest.raises(RuntimeError, match="E_SCHEDULING_FAILED"):
        svc.schedule_project(req())


def test_convert_partial_frame_tolerated(monkeypatch):
    # Only A1 of [A1, B1] scheduled: known works pass through, no failure.
    install(monkeypatch, items=[frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 8), {"c": 2})])
    out = svc.schedule_project(req())
    assert [w.id for w in out.works] == ["A1"]
    assert out.works[0].resources[0].count == 2


def test_convert_workers_string_cell(monkeypatch):
    install(
        monkeypatch,
        items=[frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), "{'a': 2}")],
    )
    out = svc.schedule_project(req())
    assert [(r.name, r.count) for r in out.works[0].resources] == [("a", 2)]


def test_convert_workers_bad_count_internal(monkeypatch):
    install(monkeypatch, items=[frame_row("A1", datetime(2026, 1, 5), datetime(2026, 1, 6), {"a": True})])
    with pytest.raises(RuntimeError, match="E_INTERNAL"):
        svc.schedule_project(req())


def test_is_domain_error_two_args_is_internal():
    assert svc._is_domain_error(RuntimeError("E_SCHEDULING_FAILED", "extra")) is False
    assert svc._is_domain_error(ValueError("E_SCHEDULING_FAILED")) is True


def test_works_datetimes_serialize_as_rfc3339():
    from datetime import datetime

    from planning_adapter.models import ScheduledWork

    w = ScheduledWork(id="A", name="n", start=datetime(2026, 1, 5), finish=datetime(2026, 1, 6), duration_days=1.0)
    dumped = w.model_dump(mode="json")
    assert dumped["start"] == "2026-01-05T00:00:00Z"
    assert dumped["finish"] == "2026-01-06T00:00:00Z"
