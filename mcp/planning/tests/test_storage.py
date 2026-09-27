"""Storage adapter: DSN handling, factory injection, LRU eviction."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import planning_adapter.storage_adapter as sa  # noqa: E402
from planning_adapter.config import RM_ADAPTER_CONN_STR_ENV_NAME  # noqa: E402

DSN_TMPL = "postgresql://u:x@h/db{}"


class _FakeAdapter:
    instances: list = []

    def __init__(self, url: str):
        self.url = url
        self.closed = False
        _FakeAdapter.instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_cache():
    _FakeAdapter.instances.clear()
    yield
    for adapter in list(sa._instances.values()):
        close = getattr(adapter, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    sa._instances.clear()


def _env(i: int) -> dict:
    return {RM_ADAPTER_CONN_STR_ENV_NAME: DSN_TMPL.format(i)}


def test_missing_or_blank_dsn_fails():
    with pytest.raises(Exception, match="E_RESOURCE_MODEL_MISSING"):
        sa.load_model_storage({}, factory=_FakeAdapter)
    with pytest.raises(Exception, match="E_RESOURCE_MODEL_MISSING"):
        sa.load_model_storage({RM_ADAPTER_CONN_STR_ENV_NAME: "  "}, factory=_FakeAdapter)
    with pytest.raises(Exception, match="E_RESOURCE_MODEL_MISSING"):
        sa.load_model_storage({RM_ADAPTER_CONN_STR_ENV_NAME: 123}, factory=_FakeAdapter)


def test_factory_receives_raw_dsn():
    seen: list = []

    def factory(url: str):
        seen.append(url)
        return _FakeAdapter(url)

    sa.load_model_storage(_env(1), factory=factory)
    assert seen == [DSN_TMPL.format(1)]


def test_factory_path_skips_cache():
    # Documented: injected factory bypasses the LRU (tests control lifetime).
    sa.load_model_storage(_env(9), factory=_FakeAdapter)
    sa.load_model_storage(_env(9), factory=_FakeAdapter)
    assert len(sa._instances) == 0
    assert len(_FakeAdapter.instances) == 2


def test_lru_evicts_oldest_and_closes(monkeypatch):
    monkeypatch.setattr(sa, "MschmAdapter", _FakeAdapter)
    for i in range(5):
        sa.load_model_storage(_env(i))
    assert len(sa._instances) == 4
    assert DSN_TMPL.format(0) not in sa._instances
    first = _FakeAdapter.instances[0]
    assert first.closed is True
    assert all(a.closed is False for a in _FakeAdapter.instances[1:])


def test_same_dsn_reuses_adapter(monkeypatch):
    monkeypatch.setattr(sa, "MschmAdapter", _FakeAdapter)
    a = sa.load_model_storage(_env(7))
    b = sa.load_model_storage(_env(7))
    assert a._adapter is b._adapter
    assert len(_FakeAdapter.instances) == 1
