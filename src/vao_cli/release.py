from __future__ import annotations

from typing import Any

from .errors import IntegrityError, ResolutionError
from .models import RemoteFile


def publication_records(release: dict[str, Any]) -> list[dict[str, Any]]:
    publication = release.get("publication")
    if not isinstance(publication, dict):
        return []
    records: list[dict[str, Any]] = []
    root = publication.get("rootRecord")
    if isinstance(root, dict):
        records.append(root)
    for member in publication.get("familyMembers", []):
        record = member.get("record") if isinstance(member, dict) else None
        if isinstance(record, dict):
            records.append(record)
    return records


def carrier_inventory(release: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for record in publication_records(release)
        for item in record.get("files", [])
        if isinstance(item, dict) and item.get("role") == "carrier"
    ]


def select_carrier_file(
    files: list[RemoteFile],
    release: dict[str, Any] | None,
    *,
    file_key: str | None = None,
    mode: str = "bootstrap",
) -> RemoteFile:
    candidates = [item for item in files if item.key.lower().endswith(".vao")]
    if file_key:
        matches = [item for item in candidates if item.key == file_key]
        if len(matches) != 1:
            raise ResolutionError(
                f"VAO file {file_key!r} does not resolve exactly once"
            )
        return matches[0]
    if isinstance(release, dict):
        names = [
            item.get("fileIdentifier")
            for item in carrier_inventory(release)
            if item.get("carrierMode") == mode
        ]
        matches = [item for item in candidates if item.key in names]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise IntegrityError(
                f"Release descriptor declares more than one {mode!r} carrier"
            )
    if len(candidates) == 1:
        return candidates[0]
    raise ResolutionError(
        f"Zenodo record contains {len(candidates)} VAO carriers and no unique {mode!r} carrier; use --file"
    )


def find_carrier_record(
    release: dict[str, Any],
    *,
    carrier_id: str,
    version_pid: str,
    record_identifier: str,
    file_identifier: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in publication_records(release):
        if (
            record.get("versionPersistentIdentifier") != version_pid
            or str(record.get("recordIdentifier")) != record_identifier
        ):
            continue
        for item in record.get("files", []):
            if (
                isinstance(item, dict)
                and item.get("role") == "carrier"
                and item.get("carrierId") == carrier_id
                and item.get("fileIdentifier") == file_identifier
            ):
                matches.append((record, item))
    if len(matches) != 1:
        raise IntegrityError(
            "Carrier-member distribution does not resolve exactly once in vao-release.json"
        )
    return matches[0]
