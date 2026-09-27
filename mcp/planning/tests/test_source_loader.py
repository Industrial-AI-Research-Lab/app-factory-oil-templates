"""Source loader contracts: SSRF guards, redirects, bounds, encodings, S3."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import planning_adapter.source_loader as sl  # noqa: E402
from planning_adapter.config import (  # noqa: E402
    PLANNING_CSV_ALLOWED_HOSTS,
    PLANNING_CSV_ALLOWED_S3_BUCKETS,
)

GOOD_HOST = "allowed.invalid"
STUB_IP = "93.184.216.34"


def _env(hosts: str | None = GOOD_HOST, buckets: str | None = "test") -> dict:
    env: dict = {}
    if hosts is not None:
        env[PLANNING_CSV_ALLOWED_HOSTS] = hosts
    if buckets is not None:
        env[PLANNING_CSV_ALLOWED_S3_BUCKETS] = buckets
    return env


class _Resp:
    def __init__(self, body: bytes = b"a,b\n", status: int = 200, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def iter_content(self, size: int = 65536):
        yield self._body

    def close(self) -> None:
        self.closed = True


class _Pinned:
    """Fake pinned client serving a script of responses."""

    def __init__(self, script: list):
        self._script = list(script)
        self.calls: list = []

    def get(self, *, url, address, tls_hostname, host_header, timeout, stream, allow_redirects, proxies):
        self.calls.append((url, address))
        assert allow_redirects is False and proxies is None
        assert address == STUB_IP
        return self._script.pop(0)


def _stub_dns(monkeypatch) -> None:
    monkeypatch.setattr(sl, "_resolve", lambda hostname, deadline: [STUB_IP])


def test_redirect_to_evil_host_forbidden(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(status=302, headers={"Location": "https://evil.invalid/x.csv"})])
    with pytest.raises(Exception, match="E_URL_FORBIDDEN"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)


def test_too_many_redirects_failed(monkeypatch):
    _stub_dns(monkeypatch)
    hops = [_Resp(status=302, headers={"Location": f"https://{GOOD_HOST}/r{i}"}) for i in range(5)]
    with pytest.raises(Exception, match="E_DOWNLOAD_FAILED"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=_Pinned(hops))


def test_http_downgrade_redirect_invalid(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(status=302, headers={"Location": "http://allowed.invalid/b.csv"})])
    with pytest.raises(Exception, match="E_URL_INVALID"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)


def test_empty_location_invalid(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(status=302, headers={"Location": "  "})])
    with pytest.raises(Exception, match="E_URL_INVALID"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)


def test_non_redirect_status_failed(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(status=304)])
    with pytest.raises(Exception, match="E_DOWNLOAD_FAILED"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)


def test_content_length_lie_rejected_early(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(body=b"x", status=200, headers={"Content-Length": "99999999"})])
    with pytest.raises(Exception, match="E_DOWNLOAD_FAILED") as exc:
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)
    assert "too large" in str(exc.value)


def test_body_over_cap_rejected(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Pinned([_Resp(body=b"y" * 10_000_001)])
    with pytest.raises(Exception, match="E_DOWNLOAD_FAILED"):
        sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=client)


def test_ipv6_loopback_literal_forbidden(monkeypatch):
    _stub_dns(monkeypatch)
    with pytest.raises(Exception, match="E_URL_FORBIDDEN"):
        sl.download_planning_csv("https://[::1]/x.csv", environ=_env(hosts="[::1]"), http_client=_Pinned([]))


def test_cp1251_fallback_and_label(monkeypatch):
    _stub_dns(monkeypatch)
    raw = "activity_id;name\nA1;Фундамент\n".encode("cp1251")
    out = sl.download_planning_csv(f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=_Pinned([_Resp(body=raw)]))
    assert out.encoding == "cp1251" and "Фундамент" in out.content


def test_cp1251_catches_any_bytes():
    # cp1251 maps all 256 byte values, so the fallback never fails:
    # E_ENCODING_UNSUPPORTED is unreachable while cp1251 is configured.
    import unittest.mock as mock

    with mock.patch.object(sl, "_resolve", lambda hostname, deadline: [STUB_IP]):
        out = sl.download_planning_csv(
            f"https://{GOOD_HOST}/a.csv", environ=_env(), http_client=_Pinned([_Resp(body=b"\xff\xfe\x00bad")])
        )
    assert out.encoding == "cp1251"


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def iter_content(self, size: int = 65536):
        yield self._data

    def close(self) -> None:
        pass


class _S3:
    def __init__(self, data: bytes = b"a,b\n", length=None):
        self._data = data
        self._length = length

    def head_object(self, Bucket: str, Key: str):
        return {} if self._length is None else {"ContentLength": self._length}

    def get_object(self, Bucket: str, Key: str):
        return {"Body": _Body(self._data)}


def test_s3_bucket_outside_allowlist_forbidden():
    with pytest.raises(Exception, match="E_URL_FORBIDDEN"):
        sl.download_planning_csv("s3://other/x.csv", environ=_env(buckets="test"), s3_client=_S3())


def test_s3_head_lie_rejected():
    with pytest.raises(Exception, match="E_DOWNLOAD_FAILED"):
        sl.download_planning_csv("s3://test/x.csv", environ=_env(), s3_client=_S3(length=50_000_000))


def test_s3_roundtrip_ok():
    out = sl.download_planning_csv("s3://test/x.csv", environ=_env(), s3_client=_S3(data="Фундамент\n".encode("cp1251")))
    assert out.encoding == "cp1251" and "Фундамент" in out.content
