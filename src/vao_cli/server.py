from __future__ import annotations

import html
import ipaddress
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .catalog import DEFAULT_COMMUNITY, Catalog
from .errors import ResolutionError, VAOCLIError
from .fetch import fetch_realization
from .output import human_size
from .resolver import VAOResolver
from .selection import parse_byte_size
from .vao import asset_rows, group_rows, manifest_summary
from .zenodo import ZenodoClient, record_publication_date, record_title


@dataclass
class _CacheItem:
    expires: float
    value: dict[str, Any]


class ResolverCache:
    def __init__(self, *, ttl: float = 300, maximum: int = 256):
        self.ttl = ttl
        self.maximum = maximum
        self._values: OrderedDict[str, _CacheItem] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(
        self, key: str, create: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item and item.expires > now:
                self._values.move_to_end(key)
                return item.value
            self._values.pop(key, None)
        value = create()
        with self._lock:
            self._values[key] = _CacheItem(now + self.ttl, value)
            self._values.move_to_end(key)
            while len(self._values) > self.maximum:
                self._values.popitem(last=False)
        return value


def run_server(
    client: ZenodoClient,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    catalog_path: Path | None = None,
    cache_ttl: float = 300,
    standard_root: Path | None = None,
) -> None:
    if not _is_loopback(host):
        raise ResolutionError(
            "The bundled resolver service is loopback-only; use 127.0.0.1, ::1, or localhost"
        )
    cache = ResolverCache(ttl=cache_ttl)
    resolver = VAOResolver(client)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"VAOResolver/{__version__}"

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self._json({"status": "ok"})
                elif parsed.path == "/":
                    self._html(_home_page())
                elif parsed.path == "/resolve":
                    doi = _one(query, "doi")
                    file_key = _optional_one(query, "file")
                    value = self._resolve(doi, file_key)
                    self._html(_result_page(value))
                elif parsed.path == "/api/community":
                    with Catalog(catalog_path) as catalog:
                        self._json(
                            {"sync": catalog.sync_status(), "records": catalog.list()}
                        )
                elif parsed.path.startswith("/api/resolve/"):
                    suffix = parsed.path[len("/api/resolve/") :]
                    if suffix.endswith("/realization"):
                        doi = unquote(suffix[: -len("/realization")].rstrip("/"))
                        self._realization(doi, query)
                    else:
                        doi = unquote(suffix)
                        self._json(self._resolve(doi, _optional_one(query, "file")))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "No such resolver endpoint")
            except (VAOCLIError, ValueError) as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except BrokenPipeError:
                pass

        def _resolve(self, doi: str, file_key: str | None) -> dict[str, Any]:
            cache_key = f"{doi}\0{file_key or ''}"

            def create() -> dict[str, Any]:
                inspection = resolver.inspect(
                    doi,
                    file_key=file_key,
                    full_conformance=True,
                    community_slug=DEFAULT_COMMUNITY,
                    standard_root=standard_root,
                )
                return {
                    "record": {
                        "requestedDOI": inspection.resolved.requested_doi,
                        "resolvedDOI": inspection.resolved.resolved_doi,
                        "conceptDOI": inspection.resolved.concept_doi,
                        "recordId": inspection.resolved.record_id,
                        "title": record_title(inspection.resolved.record),
                        "publicationDate": record_publication_date(
                            inspection.resolved.record
                        ),
                    },
                    "selectedFile": {
                        "key": inspection.selected_file.key,
                        "byteSize": inspection.selected_file.size,
                    }
                    if inspection.selected_file
                    else None,
                    "summary": manifest_summary(inspection.manifest, inspection.carrier)
                    if inspection.manifest
                    else None,
                    "assets": asset_rows(inspection.manifest, inspection.carrier)
                    if inspection.manifest
                    else [],
                    "groups": group_rows(inspection.manifest)
                    if inspection.manifest
                    else [],
                    "communities": inspection.communities,
                    "communityStatus": inspection.community_status,
                    "conformance": inspection.conformance,
                    "warnings": inspection.warnings,
                }

            return cache.get_or_create(cache_key, create)

        def _realization(self, doi: str, query: dict[str, list[str]]) -> None:
            identifier = _optional_one(query, "identifier")
            file_key = _optional_one(query, "file")
            options: dict[str, Any] = {
                "asset_id": _optional_one(query, "asset"),
                "group_id": _optional_one(query, "group"),
                "kind": _optional_one(query, "kind"),
                "quality": _optional_one(query, "quality"),
                "media_type": _optional_one(query, "media_type"),
                "capability": _optional_one(query, "capability"),
                "profile": _optional_one(query, "profile"),
                "prefer": _optional_one(query, "prefer") or "best",
                "chunks": _optional_one(query, "chunks"),
            }
            maximum = _optional_one(query, "max_bytes")
            options["max_bytes"] = parse_byte_size(maximum) if maximum else None
            if options["prefer"] not in {"best", "smallest"}:
                raise ValueError("Query parameter 'prefer' must be best or smallest")
            if _optional_one(query, "plan") in {"1", "true", "yes"}:
                report = fetch_realization(
                    client,
                    doi,
                    identifier,
                    Path("realization.bin"),
                    file_key=file_key,
                    dry_run=True,
                    standard_root=standard_root,
                    **options,
                )
                self._json(report)
                return
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="vao-resolver-", suffix=".payload"
            )
            os.close(descriptor)
            Path(temporary_name).unlink(missing_ok=True)
            try:
                report = fetch_realization(
                    client,
                    doi,
                    identifier,
                    Path(temporary_name),
                    file_key=file_key,
                    standard_root=standard_root,
                    **options,
                )
                media_type = str(
                    report.get("mediaType")
                    or mimetypes.guess_type(report["member"])[0]
                    or "application/octet-stream"
                )
                filename = (
                    Path(str(report["member"])).name.replace('"', "")
                    or "realization.bin"
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(report["byteSize"]))
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
                self.send_header("X-VAO-Resolved-DOI", str(report["resolvedDOI"]))
                if report.get("outputSHA256"):
                    self.send_header("ETag", f'"sha256:{report["outputSHA256"]}"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                with Path(temporary_name).open("rb") as source:
                    shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
            finally:
                Path(temporary_name).unlink(missing_ok=True)

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            raw = (
                json.dumps(value, ensure_ascii=False, indent=2, default=str).encode(
                    "utf-8"
                )
                + b"\n"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _html(self, value: str) -> None:
            raw = value.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json({"error": status.phrase, "message": message}, status)

        def log_message(self, format: str, *args) -> None:
            print(f"{self.address_string()} - {format % args}", flush=True)

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise ResolutionError(
            f"Cannot bind resolver service to {host}:{port}: {exc}"
        ) from exc
    print(f"VAO resolver listening on http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _one(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"Query parameter {key!r} is required exactly once")
    return values[0].strip()


def _optional_one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    if not values:
        return None
    if len(values) != 1 or not values[0].strip():
        raise ValueError(f"Query parameter {key!r} may occur at most once")
    return values[0].strip()


def _home_page() -> str:
    return _page(
        "VAO resolver",
        """
        <h1>Virtual Acoustic Object resolver</h1>
        <p>Enter a DOI for a Zenodo record containing a VAO.</p>
        <form action="/resolve" method="get">
          <label>DOI <input name="doi" required placeholder="10.5281/zenodo.…"></label>
          <button type="submit">Resolve</button>
        </form>
        """,
    )


def _result_page(value: dict[str, Any]) -> str:
    record = value["record"]
    summary = value.get("summary")
    rows = []
    for asset in value.get("assets", []):
        query = f"/api/resolve/{html.escape(record['resolvedDOI'], quote=True)}/realization?identifier={_quote_html_query(str(asset['realizationId']))}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(asset.get('label') or asset.get('assetId')))}</td>"
            f"<td>{html.escape(str(asset.get('kind') or '-'))}</td>"
            f"<td>{html.escape(str(asset.get('quality') or '-'))}</td>"
            f"<td>{html.escape(human_size(asset.get('byteSize')))}</td>"
            f"<td>{'yes' if asset.get('embedded') else 'unknown'}</td>"
            f'<td><a href="{query}">Get</a></td>'
            "</tr>"
        )
    content = [
        f"<h1>{html.escape(record['title'])}</h1>",
        f"<p><strong>Resolved DOI:</strong> {html.escape(record['resolvedDOI'])}<br>",
        f"<strong>Publication date:</strong> {html.escape(str(record.get('publicationDate') or '-'))}</p>",
    ]
    if summary:
        content.extend(
            [
                (
                    f"<p><strong>VAO:</strong> {html.escape(str(summary['formatVersion']))}; "
                    f"{summary['logicalAssetCount']} logical assets, {summary['realizationCount']} realizations, "
                    f"{html.escape(human_size(summary['totalRealizationBytes']))} quantitative extent.</p>"
                ),
                f"<p><strong>Modalities:</strong> {html.escape(', '.join(summary['modalities']) or '-')}</p>",
                "<table><thead><tr><th>Asset</th><th>Kind</th><th>Quality</th><th>Size</th><th>Embedded</th><th></th></tr></thead>",
                "<tbody>" + "".join(rows) + "</tbody></table>",
            ]
        )
    else:
        content.append("<p>No inspectable VAO manifest was found.</p>")
    for warning in value.get("warnings", []):
        content.append(f"<p class=warning>{html.escape(warning)}</p>")
    content.append('<p><a href="/">Resolve another DOI</a></p>')
    return _page(record["title"], "".join(content))


def _quote_html_query(value: str) -> str:
    from urllib.parse import quote

    return html.escape(quote(value, safe=""), quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title><style>
body{{font:16px system-ui,sans-serif;max-width:72rem;margin:3rem auto;padding:0 1rem;color:#17202a}}
input{{min-width:24rem;padding:.55rem}}button{{padding:.58rem 1rem}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;border-bottom:1px solid #d5d8dc;padding:.55rem}}.warning{{color:#8a4b08}}
</style></head><body>{body}</body></html>"""
