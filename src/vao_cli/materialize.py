from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import IntegrityError, ResolutionError
from .fetch import fetch_realization
from .local import run_reference_validator, validate_local_carrier
from .release import select_carrier_file
from .remote_zip import RemoteZipReader
from .resolver import VAOResolver
from .selection import SelectionConstraints, group_realization_ids, select_realizations
from .vao import (
    CARRIER_NAME,
    MANIFEST_NAME,
    MIMETYPE,
    RELEASE_NAME,
    json_bytes,
    strict_json,
)
from .zenodo import ZenodoClient

_EPOCH = (1980, 1, 1, 0, 0, 0)
_EXTENSIONS = {
    "application/json": ".json",
    "application/midi": ".mid",
    "application/octet-stream": ".bin",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "audio/flac": ".flac",
    "audio/midi": ".mid",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "model/gltf-binary": ".glb",
    "text/csv": ".csv",
}


def materialize_carrier(
    client: ZenodoClient,
    doi: str,
    output: Path,
    *,
    realization_ids: list[str] | None = None,
    group_ids: list[str] | None = None,
    all_realizations: bool = False,
    file_key: str | None = None,
    allow_concept: bool = True,
    dry_run: bool = False,
    constraints: SelectionConstraints | None = None,
    conformance: bool = True,
    standard_root: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Materialize an immutable custom carrier from a DOI-identified VAO release."""
    inspection = VAOResolver(client).inspect(
        doi,
        file_key=file_key,
        allow_concept=allow_concept,
        full_conformance=conformance,
        standard_root=standard_root,
    )
    manifest = inspection.manifest
    if not isinstance(manifest, dict):
        raise ResolutionError("The resolved record has no inspectable VAO manifest")
    manifest_raw = _exact_manifest_bytes(
        client,
        inspection.resolved.resolved_doi,
        file_key=file_key,
    )
    if strict_json(manifest_raw, MANIFEST_NAME) != manifest:
        raise IntegrityError("Resolved manifest bytes changed during materialization")
    selected = _selected_realizations(
        manifest,
        realization_ids=realization_ids or [],
        group_ids=group_ids or [],
        all_realizations=all_realizations,
        constraints=constraints,
    )
    selected_ids = [str(item["id"]) for item in selected]
    carrier_id = "urn:uuid:" + str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            str(manifest["release"]["id"]) + "\0" + "\0".join(selected_ids),
        )
    )
    paths = {
        identifier: _member_path(index, realization)
        for index, (identifier, realization) in enumerate(
            ((str(item["id"]), item) for item in selected), start=1
        )
    }
    complete_groups = _complete_groups(manifest, set(selected_ids))
    descriptor = {
        "id": carrier_id,
        "formatVersion": manifest["formatVersion"],
        "type": "VAOCarrier",
        "carrierMode": "custom",
        "releaseId": manifest["release"]["id"],
        "manifestByteSize": len(manifest_raw),
        "manifestSHA256": hashlib.sha256(manifest_raw).hexdigest(),
        "completeGroupIds": complete_groups,
        "embeddedRealizations": [
            {"realizationId": identifier, "path": paths[identifier]}
            for identifier in selected_ids
        ],
    }
    result: dict[str, Any] = {
        "requestedDOI": inspection.resolved.requested_doi,
        "resolvedDOI": inspection.resolved.resolved_doi,
        "releaseId": manifest["release"]["id"],
        "carrierId": carrier_id,
        "carrierMode": "custom",
        "output": str(output),
        "dryRun": dry_run,
        "realizationCount": len(selected),
        "totalByteSize": sum(int(item["byteSize"]) for item in selected),
        "completeGroupIds": complete_groups,
        "realizations": [
            {
                "realizationId": identifier,
                "member": paths[identifier],
                "byteSize": realization["byteSize"],
                "sha256": realization["sha256"],
                "mediaType": realization.get("mediaType"),
                "qualityTier": realization.get("qualityTier"),
            }
            for identifier, realization in (
                (str(item["id"]), item) for item in selected
            )
        ],
    }
    if dry_run:
        return result
    if output.exists():
        raise ResolutionError(f"Materialized carrier already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    temporary_carrier = work / output.name
    try:
        acquired: dict[str, Path] = {}
        for item in selected:
            identifier = str(item["id"])
            target = work / f"source-{len(acquired) + 1:06d}.bin"
            fetch_realization(
                client,
                inspection.resolved.resolved_doi,
                identifier,
                target,
                file_key=file_key,
                allow_concept=False,
                conformance=conformance,
                standard_root=standard_root,
                progress=progress,
            )
            acquired[identifier] = target
        _write_carrier(
            temporary_carrier,
            manifest_raw,
            descriptor,
            [(paths[str(item["id"])], acquired[str(item["id"])]) for item in selected],
        )
        local = validate_local_carrier(temporary_carrier, verify_payloads=True)
        if not local["valid"]:
            raise IntegrityError(
                "Materialized carrier failed integrity validation: "
                + "; ".join(local["errors"][:8])
            )
        reference = None
        if conformance:
            reference = run_reference_validator(
                temporary_carrier, standard_root=standard_root, required=True
            )
            if not reference["valid"]:
                raise IntegrityError(
                    "Materialized carrier failed reference validation: "
                    + (reference["stderr"] or reference["stdout"])
                )
        sha256 = _sha256_file(temporary_carrier)
        byte_size = temporary_carrier.stat().st_size
        os.replace(temporary_carrier, output)
        result.update(
            {
                "byteSize": byte_size,
                "sha256": sha256,
                "verified": True,
                "verifiedPayloadBytes": local["verifiedPayloadBytes"],
                "referenceConformance": reference,
            }
        )
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _exact_manifest_bytes(
    client: ZenodoClient, doi: str, *, file_key: str | None
) -> bytes:
    resolved = client.resolve(doi, allow_concept=False)
    client = client.for_resolved(resolved)
    files = client.files(resolved.record)
    standalone = next((item for item in files if item.key == MANIFEST_NAME), None)
    if standalone is not None:
        return client.http.get_cached_bytes(
            standalone.content_url, maximum=64 * 1024 * 1024
        )
    release_file = next((item for item in files if item.key == RELEASE_NAME), None)
    release = (
        strict_json(
            client.http.get_cached_bytes(
                release_file.content_url, maximum=16 * 1024 * 1024
            ),
            RELEASE_NAME,
        )
        if release_file is not None
        else None
    )
    remote = select_carrier_file(files, release, file_key=file_key, mode="bootstrap")
    reader = RemoteZipReader(client.http, remote.content_url, remote.size)
    return reader.read(reader.require_entry(MANIFEST_NAME), maximum=64 * 1024 * 1024)


def _selected_realizations(
    manifest: dict[str, Any],
    *,
    realization_ids: list[str],
    group_ids: list[str],
    all_realizations: bool,
    constraints: SelectionConstraints | None,
) -> list[dict[str, Any]]:
    realizations = {
        str(item.get("id")): item
        for item in manifest.get("realizations", [])
        if isinstance(item, dict) and item.get("id")
    }
    groups = {
        str(item.get("id")): item
        for item in manifest.get("assetGroups", [])
        if isinstance(item, dict) and item.get("id")
    }
    identifiers: set[str] = set(realization_ids)
    for group_id in group_ids:
        identifiers.update(group_realization_ids(groups, group_id))
    if all_realizations:
        identifiers.update(realizations)
    if constraints is not None:
        identifiers.update(
            str(item["realizationId"])
            for item in select_realizations(manifest, constraints)
        )
    if not identifiers:
        raise ResolutionError(
            "Select at least one --realization or --group, add semantic filters, or use --all"
        )
    missing = sorted(identifiers - realizations.keys())
    if missing:
        raise ResolutionError("Unknown realization identifiers: " + ", ".join(missing))
    return [realizations[item] for item in sorted(identifiers)]


def _complete_groups(manifest: dict[str, Any], selected: set[str]) -> list[str]:
    groups = {
        str(item.get("id")): item
        for item in manifest.get("assetGroups", [])
        if isinstance(item, dict) and item.get("id")
    }
    complete: list[str] = []
    for identifier in sorted(groups):
        closure = set(group_realization_ids(groups, identifier))
        if closure and closure <= selected:
            complete.append(identifier)
    return complete


def _member_path(index: int, realization: dict[str, Any]) -> str:
    digest = hashlib.sha256(str(realization["id"]).encode()).hexdigest()[:16]
    extension = _EXTENSIONS.get(str(realization.get("mediaType")), ".bin")
    return f"payload/{index:06d}-{digest}{extension}"


def _write_carrier(
    target: Path,
    manifest_raw: bytes,
    descriptor: dict[str, Any],
    members: list[tuple[str, Path]],
) -> None:
    with zipfile.ZipFile(target, "w", allowZip64=True) as archive:
        _write_bytes(archive, "mimetype", MIMETYPE)
        _write_bytes(archive, MANIFEST_NAME, manifest_raw)
        _write_bytes(archive, CARRIER_NAME, json_bytes(descriptor))
        for name, source in members:
            info = _zip_info(name)
            with (
                source.open("rb") as input_stream,
                archive.open(info, "w", force_zip64=True) as output_stream,
            ):
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)


def _write_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    archive.writestr(_zip_info(name), data)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
