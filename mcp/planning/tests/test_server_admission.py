"""Transport admission/timeout contracts for the Planning MCP server.

Covers the paths unit suites cannot reach: admission saturation and the
scheduling deadline in ``planning_mcp.server.schedule_project``. Both must
surface ``E_SCHEDULING_FAILED`` (a NameError here previously degraded them
to ``E_INTERNAL``). No network, DB, or SAMPO; the adapter call is stubbed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import planning_mcp.server as srv  # noqa: E402
from planning_adapter import E_SCHEDULING_FAILED  # noqa: E402


def _args(**kw: object) -> dict:
    """Build valid tool args hitting admission, not validation."""
    base = {"csv_url": "https://example.invalid/p.csv", "algorithm": "heft",
            "optimization_objective": "min_time"}
    base.update(kw)
    return base


def test_admission_saturation_is_scheduling_failed() -> None:
    """An occupied admission pool fails fast with E_SCHEDULING_FAILED."""
    held = 0
    try:
        while srv._ADMISSION.acquire(blocking=False):
            held += 1
        assert held > 0
        with pytest.raises(RuntimeError) as ctx:
            srv.schedule_project(**_args())
    finally:
        for _ in range(held):
            srv._ADMISSION.release()
    assert str(ctx.value) == E_SCHEDULING_FAILED


def test_scheduling_deadline_is_scheduling_failed(monkeypatch) -> None:
    """An over-deadline run fails with E_SCHEDULING_FAILED and frees the slot."""
    import threading

    started = threading.Event()

    def slow(_request):
        started.set()
        time.sleep(2)

    monkeypatch.setattr(srv, "_schedule_project", slow)
    monkeypatch.setattr(srv, "_DEADLINE_SECONDS", 0.05)
    with pytest.raises(RuntimeError) as ctx:
        srv.schedule_project(**_args())
    assert str(ctx.value) == E_SCHEDULING_FAILED
    # The running thread cannot be killed, so the slot frees only when it
    # ends (~2s): prove the done-callback path, not a leaked semaphore.
    assert started.is_set()
    assert srv._ADMISSION.acquire(timeout=10)
    srv._ADMISSION.release()


def test_unknown_symbol_is_internal_not_name_error(monkeypatch) -> None:
    """Module references resolve; unexpected adapter failures stay E_INTERNAL."""
    from planning_adapter import E_INTERNAL

    def boom(_request):
        raise OSError("disk gone")

    monkeypatch.setattr(srv, "_schedule_project", boom)
    with pytest.raises(RuntimeError) as ctx:
        srv.schedule_project(**_args())
    assert str(ctx.value) == E_INTERNAL
