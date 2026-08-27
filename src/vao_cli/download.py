from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import IntegrityError, ResolutionError, UnsupportedError
from .local import (
    ensure_reference_validator,
    run_reference_descriptor_bytes,
    run_reference_validator,
    validate_local_carrier,
)
from .models import RemoteFile, ResolvedRecord
from .vao import RELEASE_NAME
from .zenodo import ZenodoClient


def select_vao_files(
    files: list[RemoteFile], *, file_key: str | None, all_files: bool
) -> list[RemoteFile]:
    candidates = [item for item in files if item.key.lower().endswith(".vao")]
    if file_key:
        matches = [item for item in candidates if item.key == file_key]
        if len(matches) != 1:
            raise ResolutionError(
                f"VAO file {file_key!r} does not resolve exactly once"
            )
        return matches
    if all_files:
        if not candidates:
            raise ResolutionError("Zenodo record contains no .vao files")
        return candidates
    if len(candidates) != 1:
        raise ResolutionError(
            f"Zenodo record contains {len(candidates)} .vao files; use --file or --all"
        )
    return candidates


def download_vaos(
    client: ZenodoClient,
    doi: str,
    destination: Path,
    *,
    file_key: str | None = None,
    all_files: bool = False,
    allow_concept: bool = True,
    conformance: bool = True,
    standard_root: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    if conformance:
        ensure_reference_validator(standard_root)
    resolved = client.resolve(doi, allow_concept=allow_concept)
    client = client.for_resolved(resolved)
    files = client.files(resolved.record)
    selected = select_vao_files(files, file_key=file_key, all_files=all_files)
    destination.mkdir(parents=True, exist_ok=True)
    inventory = _release_inventory(
        client,
        files,
        conformance=conformance,
        standard_root=standard_root,
    )
    reports: list[dict[str, Any]] = []
    for remote in selected:
        target = destination / Path(remote.key).name
        if target.exists():
            raise ResolutionError(f"Download destination already exists: {target}")
        expected_sha256 = inventory.get(remote.key, {}).get("sha256")
        report = _download_one(
            client,
            resolved,
            remote,
            target,
            expected_sha256=expected_sha256,
            progress=progress,
        )
        local_report = validate_local_carrier(target, verify_payloads=True)
        report["carrierValidation"] = {
            "valid": local_report["valid"],
            "errors": local_report["errors"],
            "verifiedPayloadBytes": local_report["verifiedPayloadBytes"],
        }
        if not local_report["valid"]:
            target.unlink(missing_ok=True)
            raise IntegrityError(
                "Downloaded carrier is invalid: "
                + "; ".join(local_report["errors"][:8])
            )
        manifest = local_report.get("manifest")
        if conformance and (
            not isinstance(manifest, dict) or manifest.get("formatVersion") != "0.4.0"
        ):
            target.unlink(missing_ok=True)
            raise UnsupportedError(
                "Full reference conformance is available only for VAO 0.4.0; "
                "use --no-conformance only for explicitly limited legacy downloads"
            )
        if conformance:
            reference = run_reference_validator(
                target, standard_root=standard_root, required=True
            )
            report["referenceConformance"] = reference
            if not reference["valid"]:
                target.unlink(missing_ok=True)
                raise IntegrityError(
                    "Downloaded carrier failed the VAO 0.4.0 reference validator: "
                    + (reference["stderr"] or reference["stdout"])
                )
        reports.append(report)
    return reports


def _download_one(
    client: ZenodoClient,
    resolved: ResolvedRecord,
    remote: RemoteFile,
    target: Path,
    *,
    expected_sha256: str | None,
    progress: Callable[[int, int], None] | None,
) -> dict[str, Any]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".partial", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size = 0
    try:
        with (
            client.http.open(
                remote.content_url, headers={"Accept": "application/octet-stream"}
            ) as response,
            temporary.open("wb") as output,
        ):
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > remote.size:
                    raise IntegrityError(
                        f"Download exceeds Zenodo's declared size for {remote.key!r}"
                    )
                md5.update(block)
                sha256.update(block)
                output.write(block)
                if progress:
                    progress(size, remote.size)
            output.flush()
            os.fsync(output.fileno())
        if size != remote.size:
            raise IntegrityError(
                f"Downloaded {size} bytes for {remote.key!r}; expected {remote.size}"
            )
        if remote.checksum:
            algorithm, _, value = remote.checksum.partition(":")
            if algorithm.lower() == "md5" and md5.hexdigest() != value.lower():
                raise IntegrityError(f"Zenodo MD5 mismatch for {remote.key!r}")
        if expected_sha256 and sha256.hexdigest() != expected_sha256:
            raise IntegrityError(f"VAO release SHA-256 mismatch for {remote.key!r}")
        os.replace(temporary, target)
        return {
            "requestedDOI": resolved.requested_doi,
            "resolvedDOI": resolved.resolved_doi,
            "recordId": resolved.record_id,
            "file": remote.key,
            "output": str(target),
            "byteSize": size,
            "md5": md5.hexdigest(),
            "sha256": sha256.hexdigest(),
            "releaseSHA256Verified": bool(expected_sha256),
        }
    finally:
        temporary.unlink(missing_ok=True)


def _release_inventory(
    client: ZenodoClient,
    files: list[RemoteFile],
    *,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, dict[str, Any]]:
    release_file = next((item for item in files if item.key == RELEASE_NAME), None)
    if release_file is None:
        return {}
    raw = client.http.get_cached_bytes(
        release_file.content_url, maximum=16 * 1024 * 1024
    )
    if conformance:
        report = run_reference_descriptor_bytes(
            "release", raw, standard_root=standard_root, required=True
        )
        if not report["valid"]:
            raise IntegrityError(
                "VAO release descriptor failed full 0.4.0 conformance: "
                + (report["stderr"] or report["stdout"])
            )
    from .vao import strict_json

    release = strict_json(raw, RELEASE_NAME, maximum=16 * 1024 * 1024)
    publication = (
        release.get("publication")
        if isinstance(release.get("publication"), dict)
        else {}
    )
    root = (
        publication.get("rootRecord")
        if isinstance(publication.get("rootRecord"), dict)
        else {}
    )
    return {
        item.get("fileIdentifier"): item
        for item in root.get("files", [])
        if isinstance(item, dict) and item.get("fileIdentifier")
    }
