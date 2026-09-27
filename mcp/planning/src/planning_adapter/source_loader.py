"""Independent bounded csv_url loader for Planning MCP."""

import ipaddress
import os
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Literal, Protocol
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict

from .config import E_DOWNLOAD_FAILED, E_ENCODING_UNSUPPORTED, E_INTERNAL, E_URL_FORBIDDEN, E_URL_INVALID, PLANNING_CSV_ALLOWED_HOSTS, PLANNING_CSV_ALLOWED_S3_BUCKETS, PLANNING_CSV_ENCODINGS, PLANNING_FETCH_TIMEOUT_SECONDS, PLANNING_MAX_DOWNLOAD_BYTES, PLANNING_MAX_REDIRECTS
from .errors import PlanningRuntimeError, PlanningValidationError

__all__ = ("DownloadedPlanningCsv", "download_planning_csv")
# Redirect statuses only: 304 is caching, 300/305/306 are obsolete/unused.
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
# DNS runs in a pool because getaddrinfo blocks with no timeout of its own;
# 2 workers keep parallel tool calls from serializing on resolution.
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="planning-dns")


class PinnedHttpsClient(Protocol):
    """HTTPS client contract that cannot re-resolve the validated hostname.

    The caller resolves and validates the address; the client must connect
    to it directly, so DNS cannot change the target mid-request.
    """

    def get(self, *, url: str, address: str, tls_hostname: str, host_header: str,
            timeout: float, stream: bool, allow_redirects: bool,
            proxies: None) -> object:
        """GET once from the pre-resolved address without re-resolving.

        Args:
            url: Full request URL (only its path is sent on the wire).
            address: Pre-resolved, validated IP to connect to.
            tls_hostname: SNI/verification hostname.
            host_header: Value of the ``Host`` header.
            timeout: Per-request timeout in seconds.
            stream: Must stream the body for bounded draining.
            allow_redirects: Must be ``False``; redirects are revalidated manually.
            proxies: Must be ``None``; no proxying past the pin.

        Returns:
            Response object with ``status_code``, ``headers``, and a body reader.
        """


class DownloadedPlanningCsv(BaseModel):
    """Decoded Planning CSV payload with exact encoding label.

    Attributes:
        content: Decoded CSV text.
        encoding: Which of ``PLANNING_CSV_ENCODINGS`` succeeded.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str
    encoding: Literal["utf-8-sig", "cp1251"]


def _fail(code: str, hint: str) -> PlanningValidationError:
    """Build safe error without URL, body, or exception text."""
    return PlanningValidationError(code, hint)


def _remaining(deadline: float) -> float:
    """Return seconds left until the deadline.

    Raises:
        PlanningValidationError: ``E_DOWNLOAD_FAILED`` if already expired.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _fail(E_DOWNLOAD_FAILED, "download timed out")
    return remaining


def _allowlist(raw: object, *, lower: bool = True) -> frozenset[str]:
    """Split comma allowlist into a deterministic set.

    Args:
        raw: Raw env value; non-strings yield an empty set (fail closed).
        lower: Lowercase entries (hosts); keep case for bucket names.
    """
    if not isinstance(raw, str): return frozenset()
    return frozenset(p.strip().lower() if lower else p.strip() for p in raw.split(",") if p.strip())


def _resolve(hostname: str, deadline: float) -> list[str]:
    """Resolve hostname to IPs immediately before the request.

    Raises:
        PlanningValidationError: ``E_DOWNLOAD_FAILED`` on timeout or DNS failure.
    """
    import socket
    future = _DNS_EXECUTOR.submit(socket.getaddrinfo, hostname, 443, 0, socket.SOCK_STREAM)
    try:
        infos = future.result(timeout=_remaining(deadline))
    except TimeoutError:
        future.cancel()
        raise _fail(E_DOWNLOAD_FAILED, "download timed out") from None
    except Exception:
        raise _fail(E_DOWNLOAD_FAILED, "dns resolution failed") from None
    _remaining(deadline)
    out = [str(i[4][0]) for i in infos if len(i) > 4 and len(i[4]) > 0]
    if not out: raise _fail(E_DOWNLOAD_FAILED, "dns resolution failed") from None
    return out


def _reject(addresses: list[str]) -> None:
    """Reject non-public IPs (SSRF guard).

    Raises:
        PlanningValidationError: ``E_URL_FORBIDDEN`` for private, loopback,
            link-local, multicast, reserved, or unspecified addresses.
    """
    for raw in addresses:
        try: ip = ipaddress.ip_address(raw)
        except ValueError: raise _fail(E_URL_FORBIDDEN, "forbidden address") from None
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise _fail(E_URL_FORBIDDEN, "forbidden address") from None


def _header(headers: object, name: str) -> str | None:
    """Case-insensitive header lookup; ``None`` if absent or unreadable."""
    if not isinstance(headers, Mapping): return None
    try:
        want = name.lower()
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == want: return None if v is None else str(v)
    except Exception: return None
    return None


def _push(out: bytearray, chunk: object) -> None:
    """Append one chunk, enforcing the download size bound.

    Raises:
        PlanningValidationError: ``E_DOWNLOAD_FAILED`` on bad chunks or overflow.
    """
    if not chunk: return
    if isinstance(chunk, str): chunk = chunk.encode("utf-8")
    if not isinstance(chunk, (bytes, bytearray)): raise _fail(E_DOWNLOAD_FAILED, "download failed")
    out.extend(chunk)
    if len(out) > PLANNING_MAX_DOWNLOAD_BYTES: raise _fail(E_DOWNLOAD_FAILED, "download too large")


def _drain(src: object, deadline: float) -> bytes:
    """Drain bounded bytes via ``iter_content``/``iter_chunks``/``read``.

    Raises:
        PlanningValidationError: ``E_DOWNLOAD_FAILED`` on read errors or overflow.
    """
    out = bytearray()
    for attr in ("iter_content", "iter_chunks"):
        fn = getattr(src, attr, None)
        if callable(fn):
            try:
                it = fn(65536)
                for chunk in it:
                    _remaining(deadline)
                    _push(out, chunk)
            except PlanningValidationError:
                raise
            except Exception:
                raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
            return bytes(out)
    rd = getattr(src, "read", None)
    if callable(rd):
        try:
            while True:
                _remaining(deadline)
                chunk = rd(65536)
                if not chunk:
                    break
                _push(out, chunk)
        except PlanningValidationError:
            raise
        except Exception:
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
        return bytes(out)
    raise _fail(E_DOWNLOAD_FAILED, "download failed")


def _get(url: str, client: PinnedHttpsClient | None, *, hostname: str,
         address: str, deadline: float) -> object:
    """GET once without redirects, pinned to the validated address.

    Args:
        client: Injected client (tests); urllib3 fallback otherwise.
    """
    if client is not None:
        try:
            return client.get(
                url=url, address=address, tls_hostname=hostname,
                host_header=hostname, timeout=_remaining(deadline), stream=True,
                allow_redirects=False, proxies=None,
            )
        except PlanningValidationError:
            raise
        except Exception:
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
    try:
        import urllib3
        parts = urlsplit(url)
        port = parts.port or 443
        path = parts.path + (("?" + parts.query) if parts.query else "")
        pool = urllib3.HTTPSConnectionPool(
            address, port=port, server_hostname=hostname, assert_hostname=hostname,
            cert_reqs="CERT_REQUIRED", timeout=_remaining(deadline),
        )
        response = pool.urlopen("GET", path, headers={"Host": hostname}, redirect=False, preload_content=False, retries=False)
        response.status_code = response.status
        response.close = lambda close=response.close, clear=pool.close: (close(), clear())
        return response
    except PlanningValidationError:
        raise
    except Exception:
        raise _fail(E_DOWNLOAD_FAILED, "download failed") from None


def _check_https(url: str, allowed: frozenset[str], deadline: float) -> str:
    """Validate scheme, credentials, allowlist, and resolved IPs.

    Returns:
        Lowercased hostname for the pinned request.
    """
    try: parts = urlsplit(url)
    except ValueError: raise _fail(E_URL_INVALID, "invalid URL") from None
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
        raise _fail(E_URL_INVALID, "URL must not include credentials or fragment") if parts.scheme == "https" else _fail(E_URL_INVALID, "unsupported URL scheme")
    host = parts.hostname
    if not host or not parts.path or parts.path == "/": raise _fail(E_URL_INVALID, "URL must include host and path")
    low = host.lower()
    if low not in allowed: raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
    try: literal: object = ipaddress.ip_address(low.strip("[]"))
    except ValueError: literal = None
    # Pinned per request: each hop is re-resolved, validated, then pinned;
    # no cross-request DNS cache is kept.
    _reject([str(literal)] if literal is not None else _resolve(low, deadline))
    return low


def _fetch_https(url: str, allowed: frozenset[str], client: PinnedHttpsClient | None,
                 deadline: float) -> bytes:
    """Fetch HTTPS bytes, revalidating every redirect hop.

    Same-origin hops stay trusted; cross-origin hops must be allowlisted.
    """
    try: origin = (urlsplit(url).hostname or "").lower()
    except ValueError: raise _fail(E_URL_INVALID, "invalid URL") from None
    current = url
    for attempt in range(PLANNING_MAX_REDIRECTS + 1):
        _remaining(deadline)
        hostname = _check_https(current, allowed, deadline)
        try:
            addresses = [str(ipaddress.ip_address(hostname))]
        except ValueError:
            addresses = _resolve(hostname, deadline)
        _reject(addresses)
        resp = _get(current, client, hostname=hostname, address=addresses[0], deadline=deadline)
        try:
            if getattr(resp, "status_code", None) in _REDIRECTS:
                if attempt >= PLANNING_MAX_REDIRECTS: raise _fail(E_DOWNLOAD_FAILED, "too many redirects")
                loc = _header(getattr(resp, "headers", {}), "location")
                if not loc or not loc.strip(): raise _fail(E_URL_INVALID, "invalid redirect")
                nxt = urljoin(current, loc.strip())
                try: rparts = urlsplit(nxt)
                except ValueError: raise _fail(E_URL_INVALID, "invalid redirect") from None
                if rparts.scheme != "https" or not rparts.hostname or not rparts.path or rparts.path == "/": raise _fail(E_URL_INVALID, "invalid redirect")
                new_host = rparts.hostname.lower()
                if new_host != origin and new_host not in allowed: raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
                current = nxt
                continue
            if getattr(resp, "status_code", None) != 200: raise _fail(E_DOWNLOAD_FAILED, "download failed")
            declared = _header(getattr(resp, "headers", {}), "content-length")
            if declared is not None:
                try: size: int = int(declared.strip())
                except (ValueError, AttributeError): raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
                if size < 0: raise _fail(E_DOWNLOAD_FAILED, "download failed")
                if size > PLANNING_MAX_DOWNLOAD_BYTES: raise _fail(E_DOWNLOAD_FAILED, "download too large")
            return _drain(resp, deadline)
        finally:
            try: fn = getattr(resp, "close", None); fn() if callable(fn) else None
            except Exception: pass
    raise _fail(E_DOWNLOAD_FAILED, "too many redirects")


def _split_s3(url: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``.

    Raises:
        PlanningValidationError: ``E_URL_INVALID`` on bad shape or credentials.
    """
    try: parts = urlsplit(url)
    except ValueError: raise _fail(E_URL_INVALID, "invalid URL") from None
    if parts.scheme != "s3" or parts.username or parts.password or parts.fragment:
        raise _fail(E_URL_INVALID, "URL must include bucket and key") if parts.scheme == "s3" else _fail(E_URL_INVALID, "unsupported URL scheme")
    bucket, key = parts.netloc, parts.path.lstrip("/")
    if not bucket or not key or not key.strip("/"): raise _fail(E_URL_INVALID, "URL must include bucket and key")
    return bucket, key


def _fetch_s3(url: str, allowed: frozenset[str], s3: object | None,
              deadline: float) -> bytes:
    """Fetch S3 bytes after allowlist and ``head_object`` size check.

    Args:
        s3: Injected client (tests); boto3 client otherwise.
    """
    bucket, key = _split_s3(url)
    if bucket not in allowed: raise _fail(E_URL_FORBIDDEN, "bucket not allowlisted")
    client: object = s3
    if client is None:
        try:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", config=Config(
                connect_timeout=PLANNING_FETCH_TIMEOUT_SECONDS,
                read_timeout=PLANNING_FETCH_TIMEOUT_SECONDS,
                retries={"max_attempts": 3, "mode": "standard"},
            ))
        except Exception: raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
    head_fn = getattr(client, "head_object", None)
    if callable(head_fn):
        try: head = head_fn(Bucket=bucket, Key=key)
        except PlanningValidationError:
            raise
        except Exception:
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
        if isinstance(head, Mapping):
            raw_len: object = head.get("ContentLength", head.get("Content-Length"))
            if raw_len is not None:
                try: size: int = int(str(raw_len).strip())
                except (ValueError, AttributeError): raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
                if size < 0: raise _fail(E_DOWNLOAD_FAILED, "download failed")
                if size > PLANNING_MAX_DOWNLOAD_BYTES: raise _fail(E_DOWNLOAD_FAILED, "download too large")
    get_fn = getattr(client, "get_object", None)
    if not callable(get_fn): raise _fail(E_DOWNLOAD_FAILED, "download failed")
    try: obj = get_fn(Bucket=bucket, Key=key)
    except PlanningValidationError:
        raise
    except Exception:
        raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
    body = obj.get("Body", obj.get("body")) if isinstance(obj, Mapping) else (getattr(obj, "Body", None) or getattr(obj, "body", None))
    if body is None: raise _fail(E_DOWNLOAD_FAILED, "download failed")
    try: return _drain(body, deadline)
    finally:
        try: fn = getattr(body, "close", None); fn() if callable(fn) else None
        except Exception: pass


def _decode(raw: bytes) -> tuple[str, str]:
    """Decode bytes, trying ``PLANNING_CSV_ENCODINGS`` in order.

    Returns:
        ``(text, encoding_used)``.
    """
    for enc in PLANNING_CSV_ENCODINGS:
        try: return raw.decode(enc), enc
        except (UnicodeDecodeError, ValueError, LookupError): continue
    raise _fail(E_ENCODING_UNSUPPORTED, "unsupported encoding")


def download_planning_csv(csv_url: object, *, environ: Mapping[str, str] | None = None, http_client: PinnedHttpsClient | None = None, s3_client: object | None = None) -> DownloadedPlanningCsv:
    """Download one Planning CSV over HTTPS or S3 with bounds and safe errors.

    Args:
        csv_url: ``https`` or ``s3`` URL of the source file.
        environ: Environment mapping with allowlists; defaults to ``os.environ``.
        http_client: Optional pinned HTTPS client (tests); urllib3 fallback otherwise.
        s3_client: Optional S3 client (tests); boto3 client otherwise.

    Returns:
        Decoded payload with the exact encoding label.

    Raises:
        PlanningValidationError: Safe ``E_*`` code, no URLs or bodies included.
        PlanningRuntimeError: ``E_INTERNAL`` on unexpected failures.
    """
    try:
        deadline = time.monotonic() + PLANNING_FETCH_TIMEOUT_SECONDS
        if isinstance(csv_url, bool) or not isinstance(csv_url, str): raise _fail(E_URL_INVALID, "invalid URL")
        target = csv_url.strip()
        if not target: raise _fail(E_URL_INVALID, "invalid URL")
        env: Mapping[str, str] = environ if environ is not None else os.environ
        try: scheme = urlsplit(target).scheme
        except ValueError: raise _fail(E_URL_INVALID, "invalid URL") from None
        if scheme == "https":
            if not (allowed_hosts := _allowlist(env.get(PLANNING_CSV_ALLOWED_HOSTS) if isinstance(env, Mapping) else None, lower=True)): raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
            raw = _fetch_https(target, allowed_hosts, http_client, deadline)
        elif scheme == "s3":
            if not (allowed_buckets := _allowlist(env.get(PLANNING_CSV_ALLOWED_S3_BUCKETS) if isinstance(env, Mapping) else None, lower=False)): raise _fail(E_URL_FORBIDDEN, "bucket not allowlisted")
            raw = _fetch_s3(target, allowed_buckets, s3_client, deadline)
        else: raise _fail(E_URL_INVALID, "unsupported URL scheme")
        if not raw: raise _fail(E_DOWNLOAD_FAILED, "download failed")
        text, encoding = _decode(raw)
        try: return DownloadedPlanningCsv(content=text, encoding=encoding)
        except Exception: raise PlanningRuntimeError(E_INTERNAL) from None
    except PlanningValidationError:
        raise
    except PlanningRuntimeError:
        raise
    except Exception: raise PlanningRuntimeError(E_INTERNAL) from None
