from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .cache import PersistentCache
from .errors import IntegrityError, NetworkError
from .models import ZenodoInstance
from .vao import strict_json

USER_AGENT = f"vao-cli/{__version__} (+https://github.com/modavis-project/vao-cli)"
CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, instance: ZenodoInstance):
        super().__init__()
        self.instance = instance

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        target = urljoin(req.full_url, newurl)
        _require_trusted_url(target, self.instance)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _require_trusted_url(url: str, instance: ZenodoInstance) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in instance.allowed_hosts:
        raise NetworkError(
            f"Blocked URL outside the trusted {instance.name} Zenodo adapter: {url}"
        )
    if parsed.username or parsed.password:
        raise NetworkError("Credentials in URLs are prohibited")


class HTTPClient:
    def __init__(
        self,
        instance: ZenodoInstance,
        *,
        timeout: float = 30,
        retries: int = 2,
        cache: PersistentCache | None = None,
    ):
        self.instance = instance
        self.timeout = timeout
        self.retries = retries
        self.cache = cache
        self._opener = build_opener(_SafeRedirectHandler(instance))

    def _request(self, url: str, *, headers: dict[str, str] | None = None) -> Request:
        _require_trusted_url(url, self.instance)
        values = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if headers:
            values.update(headers)
        return Request(url, headers=values)

    @contextmanager
    def open(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> Iterator[BinaryIO]:
        request = self._request(url, headers=headers)
        last_error: Exception | None = None
        response: BinaryIO | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._opener.open(request, timeout=self.timeout)
                _require_trusted_url(response.geturl(), self.instance)
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    break
                retry_after = exc.headers.get("Retry-After")
                delay = (
                    min(float(retry_after), 5.0)
                    if retry_after and retry_after.isdigit()
                    else 0.25 * (2**attempt)
                )
                time.sleep(delay)
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                time.sleep(0.25 * (2**attempt))
        if response is None:
            raise NetworkError(f"Zenodo request failed for {url}: {last_error}")
        try:
            yield response
        finally:
            response.close()

    def get_bytes(
        self, url: str, *, maximum: int, headers: dict[str, str] | None = None
    ) -> bytes:
        with self.open(url, headers=headers) as response:
            data = response.read(maximum + 1)
            if len(data) > maximum:
                raise NetworkError(f"Response exceeds the {maximum}-byte safety limit")
            return data

    def get_json(self, url: str, *, maximum: int = 16 * 1024 * 1024) -> dict:
        _require_trusted_url(url, self.instance)
        raw = self.cache.get("json", url) if self.cache else None
        if raw is None:
            raw = self.get_bytes(url, maximum=maximum)
            if self.cache:
                self.cache.put("json", url, raw, ttl=300)
        try:
            value = strict_json(raw, "Zenodo JSON", maximum=maximum)
        except IntegrityError as exc:
            raise NetworkError(f"Zenodo returned invalid JSON: {exc}") from exc
        return value

    def get_cached_bytes(self, url: str, *, maximum: int, ttl: float = 300) -> bytes:
        _require_trusted_url(url, self.instance)
        raw = self.cache.get("bytes", url) if self.cache else None
        if raw is not None:
            if len(raw) > maximum:
                raise IntegrityError("Cached object exceeds its current safety limit")
            return raw
        raw = self.get_bytes(url, maximum=maximum)
        if self.cache:
            self.cache.put("bytes", url, raw, ttl=ttl)
        return raw

    def get_range(self, url: str, start: int, end: int) -> tuple[bytes, Message]:
        _require_trusted_url(url, self.instance)
        if start < 0 or end < start:
            raise ValueError("Invalid byte range")
        expected = end - start + 1
        identity = f"{url}\0{start}\0{end}"
        cached = (
            self.cache.get("range", identity)
            if self.cache and expected <= 16 * 1024 * 1024
            else None
        )
        if cached is not None:
            if len(cached) != expected:
                raise IntegrityError("Cached range has an invalid length")
            return cached, Message()
        with self.open(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "Accept": "application/octet-stream",
            },
        ) as response:
            status = getattr(response, "status", response.getcode())
            data = response.read(expected + 1)
            if status != 206:
                raise NetworkError(f"Range endpoint returned HTTP {status}, not 206")
            content_encoding = response.headers.get("Content-Encoding")
            if content_encoding and content_encoding.lower() != "identity":
                raise IntegrityError(
                    f"Range response used unexpected content encoding {content_encoding!r}"
                )
            if len(data) != expected:
                raise IntegrityError(
                    f"Range response length is {len(data)}, expected {expected}"
                )
            content_range = response.headers.get("Content-Range")
            match = CONTENT_RANGE.fullmatch(content_range or "")
            if (
                match is None
                or int(match.group(1)) != start
                or int(match.group(2)) != end
                or (match.group(3) != "*" and int(match.group(3)) <= end)
            ):
                raise IntegrityError(f"Unexpected Content-Range: {content_range}")
            if self.cache and expected <= 16 * 1024 * 1024:
                self.cache.put("range", identity, data, ttl=300)
            return data, response.headers
