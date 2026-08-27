from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from .cache import PersistentCache
from .doi import normalize_doi, zenodo_record_hint
from .errors import NetworkError, ResolutionError
from .http import HTTPClient
from .models import (
    INSTANCES,
    PRODUCTION,
    Relation,
    RemoteFile,
    ResolvedRecord,
    ZenodoInstance,
)


def _record_doi(record: dict[str, Any]) -> str:
    value = (
        record.get("doi")
        or record.get("pids", {}).get("doi", {}).get("identifier")
        or ""
    )
    return str(value).lower()


def _concept_doi(record: dict[str, Any]) -> str | None:
    value = record.get("conceptdoi")
    if not value:
        value = (
            record.get("parent", {}).get("pids", {}).get("doi", {}).get("identifier")
        )
    return str(value).lower() if value else None


def record_id(record: dict[str, Any]) -> str:
    value = record.get("id", record.get("recid", ""))
    return str(value)


def record_title(record: dict[str, Any]) -> str:
    metadata = (
        record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    )
    return str(metadata.get("title") or record.get("title") or record_id(record))


def record_publication_date(record: dict[str, Any]) -> str | None:
    metadata = (
        record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    )
    value = metadata.get("publication_date") or record.get("created")
    return str(value) if value else None


class ZenodoClient:
    """Read-only, host-pinned Zenodo Records and Communities client."""

    def __init__(
        self,
        instance: ZenodoInstance = PRODUCTION,
        *,
        timeout: float = 30,
        cache: PersistentCache | None = None,
    ):
        self.instance = instance
        self.timeout = timeout
        self.cache = cache
        self.http = HTTPClient(instance, timeout=timeout, cache=cache)

    @classmethod
    def for_name(
        cls,
        name: str,
        *,
        timeout: float = 30,
        cache: PersistentCache | None = None,
    ) -> ZenodoClient:
        try:
            return cls(INSTANCES[name], timeout=timeout, cache=cache)
        except KeyError as exc:
            raise ResolutionError(f"Unknown Zenodo instance {name!r}") from exc

    def get_record(self, identifier: str) -> dict[str, Any]:
        return self.http.get_json(
            f"{self.instance.api_base}/records/{quote(str(identifier), safe='')}"
        )

    def for_resolved(self, resolved: ResolvedRecord) -> ZenodoClient:
        if resolved.instance.name == self.instance.name:
            return self
        return ZenodoClient(resolved.instance, timeout=self.timeout, cache=self.cache)

    def resolve(self, value: str, *, allow_concept: bool = True) -> ResolvedRecord:
        requested = normalize_doi(value)
        hint = zenodo_record_hint(requested)
        if hint:
            instance, hinted_id = hint
            if instance.name != self.instance.name:
                delegated = ZenodoClient(
                    instance, timeout=self.timeout, cache=self.cache
                )
                return delegated.resolve(requested, allow_concept=allow_concept)
            record = self.get_record(hinted_id)
            actual = _record_doi(record)
            concept = _concept_doi(record)
            requested_was_concept = concept == requested and actual != requested
            if actual != requested and not requested_was_concept:
                raise ResolutionError(
                    f"Record {hinted_id} resolved to DOI {actual!r}, not {requested!r}"
                )
            if requested_was_concept and not allow_concept:
                raise ResolutionError(
                    f"{requested} is a concept DOI; exact resolution requires version DOI {actual}"
                )
            return ResolvedRecord(
                requested_doi=requested,
                resolved_doi=actual,
                concept_doi=concept,
                record_id=record_id(record),
                instance=self.instance,
                record=record,
                requested_was_concept=requested_was_concept,
            )

        # Zenodo may contain a record that uses an externally assigned DOI.
        query = urlencode(
            {"q": f'doi:"{requested}"', "all_versions": "true", "size": "10"}
        )
        response = self.http.get_json(f"{self.instance.api_base}/records?{query}")
        hits = _hits(response)
        matches = [record for record in hits if _record_doi(record) == requested]
        if len(matches) != 1:
            raise ResolutionError(
                f"Exact DOI search on {self.instance.identity} returned {len(matches)} matching records"
            )
        record = matches[0]
        return ResolvedRecord(
            requested_doi=requested,
            resolved_doi=_record_doi(record),
            concept_doi=_concept_doi(record),
            record_id=record_id(record),
            instance=self.instance,
            record=record,
            requested_was_concept=False,
        )

    def community(self, slug: str) -> dict[str, Any]:
        safe_slug = quote(slug.strip(), safe="-")
        return self.http.get_json(f"{self.instance.api_base}/communities/{safe_slug}")

    def record_communities(self, resolved: ResolvedRecord) -> list[dict[str, Any]]:
        bound = self.for_resolved(resolved)
        link = resolved.record.get("links", {}).get("communities")
        if not isinstance(link, str):
            link = (
                f"{resolved.instance.api_base}/records/{resolved.record_id}/communities"
            )
        return _hits(bound.http.get_json(link))

    def community_records(
        self, slug: str, *, maximum_records: int = 10_000
    ) -> list[dict[str, Any]]:
        community = self.community(slug)
        link = community.get("links", {}).get("records")
        if not isinstance(link, str):
            community_id = community.get("id")
            if not community_id:
                raise NetworkError("Community response has no records endpoint")
            link = f"{self.instance.api_base}/communities/{community_id}/records"
        separator = "&" if "?" in link else "?"
        # Unauthenticated community requests are capped at 25 by Zenodo.
        next_url: str | None = f"{link}{separator}{urlencode({'size': 25})}"
        result: list[dict[str, Any]] = []
        visited: set[str] = set()
        while next_url:
            if next_url in visited:
                raise NetworkError("Community pagination contains a cycle")
            visited.add(next_url)
            page = self.http.get_json(next_url)
            result.extend(_hits(page))
            if len(result) > maximum_records:
                raise NetworkError(
                    f"Community exceeds the {maximum_records}-record safety limit"
                )
            candidate = page.get("links", {}).get("next")
            next_url = str(candidate) if candidate else None
        return result

    def versions(
        self, resolved: ResolvedRecord, *, maximum: int = 10_000
    ) -> list[dict[str, Any]]:
        bound = self.for_resolved(resolved)
        link = resolved.record.get("links", {}).get("versions")
        if not isinstance(link, str):
            link = f"{resolved.instance.api_base}/records/{resolved.record_id}/versions"
        separator = "&" if "?" in link else "?"
        next_url: str | None = (
            f"{link}{separator}{urlencode({'size': 25, 'sort': 'version'})}"
        )
        result: list[dict[str, Any]] = []
        visited: set[str] = set()
        while next_url:
            if next_url in visited:
                raise NetworkError("Version pagination contains a cycle")
            visited.add(next_url)
            page = bound.http.get_json(next_url)
            result.extend(_hits(page))
            if len(result) > maximum:
                raise NetworkError(
                    f"Version chain exceeds the {maximum}-record safety limit"
                )
            candidate = page.get("links", {}).get("next")
            next_url = str(candidate) if candidate else None
        return result

    def files(self, record: dict[str, Any]) -> list[RemoteFile]:
        raw = record.get("files", [])
        values: list[dict[str, Any]] = []
        if isinstance(raw, list):
            values = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            entries = raw.get("entries", {})
            if isinstance(entries, dict):
                values = [
                    dict(item, key=item.get("key", key))
                    for key, item in entries.items()
                    if isinstance(item, dict)
                ]
            elif isinstance(entries, list):
                values = [item for item in entries if isinstance(item, dict)]
        result: list[RemoteFile] = []
        for item in values:
            key = item.get("key") or item.get("filename")
            links = item.get("links") if isinstance(item.get("links"), dict) else {}
            content = links.get("content") or links.get("self")
            if not isinstance(key, str) or not isinstance(content, str):
                continue
            # Older API records use `self` for the content URL. New file-detail
            # links named `self` identify JSON metadata, so prefer explicit content.
            if (
                not content.rstrip("/").endswith("/content")
                and "/api/records/" in content
            ):
                content = f"{content.rstrip('/')}/content"
            try:
                size = int(item.get("size", item.get("filesize", -1)))
            except (TypeError, ValueError):
                continue
            if size < 0:
                continue
            result.append(
                RemoteFile(
                    key=key,
                    size=size,
                    checksum=str(item["checksum"]) if item.get("checksum") else None,
                    content_url=content,
                    file_id=str(item.get("file_id") or item.get("id"))
                    if item.get("file_id") or item.get("id")
                    else None,
                    version_id=str(item["version_id"])
                    if item.get("version_id")
                    else None,
                )
            )
        return result

    def relations(self, resolved: ResolvedRecord) -> list[Relation]:
        metadata = (
            resolved.record.get("metadata")
            if isinstance(resolved.record.get("metadata"), dict)
            else {}
        )
        raw = metadata.get("related_identifiers", [])
        result: list[Relation] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                identifier = item.get("identifier")
                relation = item.get("relation") or item.get("relation_type")
                resource_type = item.get("resource_type")
                if isinstance(resource_type, dict):
                    resource_type = resource_type.get("id") or resource_type.get(
                        "title"
                    )
                if identifier and relation:
                    result.append(
                        Relation(
                            str(relation),
                            str(identifier),
                            str(resource_type) if resource_type else None,
                        )
                    )
        if resolved.concept_doi and resolved.concept_doi != resolved.resolved_doi:
            result.append(
                Relation(
                    "isVersionOf",
                    f"https://doi.org/{resolved.concept_doi}",
                    "dataset",
                    "zenodo-version-chain",
                )
            )
        return result


def _hits(response: dict[str, Any]) -> list[dict[str, Any]]:
    hits = response.get("hits", {})
    values = hits.get("hits", []) if isinstance(hits, dict) else []
    return [item for item in values if isinstance(item, dict)]
