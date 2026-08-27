from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ZenodoInstance:
    name: str
    identity: str
    api_base: str
    allowed_hosts: frozenset[str]
    doi_prefixes: tuple[str, ...]


PRODUCTION = ZenodoInstance(
    name="production",
    identity="https://zenodo.org",
    api_base="https://zenodo.org/api",
    allowed_hosts=frozenset({"zenodo.org"}),
    doi_prefixes=("10.5281/zenodo.",),
)

SANDBOX = ZenodoInstance(
    name="sandbox",
    identity="https://sandbox.zenodo.org",
    api_base="https://sandbox.zenodo.org/api",
    allowed_hosts=frozenset({"sandbox.zenodo.org"}),
    doi_prefixes=("10.5072/zenodo.",),
)

INSTANCES = {item.name: item for item in (PRODUCTION, SANDBOX)}


@dataclass(frozen=True)
class ResolvedRecord:
    requested_doi: str
    resolved_doi: str
    concept_doi: str | None
    record_id: str
    instance: ZenodoInstance
    record: dict[str, Any]
    requested_was_concept: bool = False


@dataclass(frozen=True)
class RemoteFile:
    key: str
    size: int
    checksum: str | None
    content_url: str
    file_id: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class Relation:
    relation: str
    identifier: str
    resource_type: str | None = None
    source: str = "metadata"


@dataclass
class VAOInspection:
    resolved: ResolvedRecord
    selected_file: RemoteFile | None
    manifest: dict[str, Any] | None
    carrier: dict[str, Any] | None
    release_descriptor: dict[str, Any] | None
    archive_entries: list[dict[str, Any]] = field(default_factory=list)
    communities: list[dict[str, Any]] = field(default_factory=list)
    community_status: str = "unknown"
    conformance: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
