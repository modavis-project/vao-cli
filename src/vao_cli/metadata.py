from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import IntegrityError, ResolutionError, UnsupportedError
from .local import run_reference_validator, validate_local_carrier
from .vao import CARRIER_NAME, MANIFEST_NAME, MIMETYPE, strict_json

EDITABLE_FORMAT = "vao-metadata-edit/1"


def _read_carrier(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    report = validate_local_carrier(path, verify_payloads=False)
    if not report["valid"]:
        raise IntegrityError(
            "Input VAO carrier is invalid: " + "; ".join(report["errors"][:8])
        )
    manifest = report["manifest"]
    carrier = report["carrier"]
    if not isinstance(manifest, dict) or not isinstance(carrier, dict):
        raise IntegrityError("Input VAO carrier has no manifest/carrier descriptor")
    if manifest.get("formatVersion") != "0.4.0":
        raise UnsupportedError(
            "Metadata editing is defined for VAO 0.4.0 carriers only"
        )
    return manifest, carrier


def metadata_projection(path: Path) -> dict[str, Any]:
    manifest, _carrier = _read_carrier(path)
    scientific = (
        manifest.get("scientific")
        if isinstance(manifest.get("scientific"), dict)
        else {}
    )
    discovery = (
        manifest.get("discovery") if isinstance(manifest.get("discovery"), dict) else {}
    )
    release = (
        manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
    )
    return {
        "editorFormat": EDITABLE_FORMAT,
        "source": str(path.resolve()),
        "objectId": manifest.get("id"),
        "title": deepcopy(manifest.get("title")),
        "description": deepcopy(manifest.get("description")),
        "contentVersion": release.get("contentVersion"),
        "agents": deepcopy(scientific.get("agents", [])),
        "discovery": {
            "resourceType": discovery.get("resourceType", "Dataset"),
            "creatorAgentIds": deepcopy(discovery.get("creatorAgentIds", [])),
            "contributorAgentIds": deepcopy(discovery.get("contributorAgentIds", [])),
            "relatedIdentifiers": deepcopy(discovery.get("relatedIdentifiers", [])),
            "fundingReferences": deepcopy(discovery.get("fundingReferences", [])),
            "subjects": deepcopy(discovery.get("subjects", [])),
            "instrumentIdentifiers": deepcopy(
                discovery.get("instrumentIdentifiers", [])
            ),
        },
    }


def write_projection(path: Path, output: Path | None = None) -> dict[str, Any]:
    value = metadata_projection(path)
    if output:
        if output.exists():
            raise ResolutionError(f"Metadata output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return value


def apply_metadata(
    source: Path,
    document: Path,
    output: Path,
    *,
    standard_root: Path | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise ResolutionError(f"Output VAO already exists: {output}")
    manifest, carrier = _read_carrier(source)
    edited = strict_json(document.read_bytes(), str(document), maximum=16 * 1024 * 1024)
    _validate_projection(edited, manifest)
    updated = deepcopy(manifest)
    updated["title"] = deepcopy(edited["title"])
    if edited.get("description") is None:
        updated.pop("description", None)
    else:
        updated["description"] = deepcopy(edited["description"])
    discovery = deepcopy(edited["discovery"])
    if not discovery.get("instrumentIdentifiers"):
        discovery.pop("instrumentIdentifiers", None)
    updated["discovery"] = discovery
    scientific = deepcopy(updated.get("scientific", {}))
    scientific["agents"] = deepcopy(edited["agents"])
    updated["scientific"] = scientific
    old_release = deepcopy(updated["release"])
    updated["release"] = {
        "id": f"urn:uuid:{uuid.uuid4()}",
        "revision": int(old_release.get("revision", 0)) + 1,
        "contentVersion": edited["contentVersion"],
        "supersedesReleaseId": old_release["id"],
    }
    updated["modifiedAt"] = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    manifest_raw = (
        json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        )
        + b"\n"
    )
    updated_carrier = deepcopy(carrier)
    updated_carrier["formatVersion"] = "0.4.0"
    updated_carrier["releaseId"] = updated["release"]["id"]
    updated_carrier["manifestByteSize"] = len(manifest_raw)
    updated_carrier["manifestSHA256"] = hashlib.sha256(manifest_raw).hexdigest()
    carrier_raw = (
        json.dumps(
            updated_carrier, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        + b"\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        _rewrite_carrier(source, temporary, manifest_raw, carrier_raw)
        local_report = validate_local_carrier(temporary, verify_payloads=True)
        if not local_report["valid"]:
            raise IntegrityError(
                "Edited carrier is invalid: " + "; ".join(local_report["errors"][:8])
            )
        reference = run_reference_validator(
            temporary, standard_root=standard_root, required=True
        )
        if not reference["valid"]:
            raise IntegrityError(
                "Edited carrier failed the VAO 0.4.0 reference validator: "
                + (reference["stderr"] or reference["stdout"])
            )
        os.replace(temporary, output)
        return {
            "source": str(source),
            "output": str(output),
            "oldReleaseId": old_release["id"],
            "newReleaseId": updated["release"]["id"],
            "revision": updated["release"]["revision"],
            "contentVersion": updated["release"]["contentVersion"],
            "byteSize": output.stat().st_size,
            "verifiedPayloadBytes": local_report["verifiedPayloadBytes"],
            "referenceConformance": reference,
        }
    finally:
        temporary.unlink(missing_ok=True)


def edit_metadata(
    source: Path,
    output: Path,
    *,
    editor: str | None = None,
    standard_root: Path | None = None,
) -> dict[str, Any]:
    projection = metadata_projection(source)
    descriptor, name = tempfile.mkstemp(prefix="vao-metadata-", suffix=".json")
    os.close(descriptor)
    document = Path(name)
    try:
        document.write_text(
            json.dumps(projection, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command = _editor_command(editor, document)
        process = subprocess.run(command, check=False)
        if process.returncode != 0:
            raise ResolutionError(
                f"Metadata editor exited with status {process.returncode}"
            )
        return apply_metadata(source, document, output, standard_root=standard_root)
    finally:
        document.unlink(missing_ok=True)


def _editor_command(editor: str | None, document: Path) -> list[str]:
    configured = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if configured:
        return [*shlex.split(configured), str(document)]
    if sys.platform == "darwin":
        return ["open", "-W", "-t", str(document)]
    if os.name == "nt":
        return ["notepad", str(document)]
    available = shutil.which("sensible-editor") or shutil.which("vi")
    if available:
        return [available, str(document)]
    raise ResolutionError(
        "No editor configured; set VISUAL or EDITOR, or pass --editor"
    )


def _validate_projection(document: dict[str, Any], manifest: dict[str, Any]) -> None:
    if document.get("editorFormat") != EDITABLE_FORMAT:
        raise IntegrityError(
            f"Metadata document must declare editorFormat {EDITABLE_FORMAT!r}"
        )
    if document.get("objectId") != manifest.get("id"):
        raise IntegrityError("Metadata document objectId does not match the source VAO")
    if not isinstance(document.get("title"), (str, dict)):
        raise IntegrityError("Metadata title must be a string or localized object")
    if (
        not isinstance(document.get("contentVersion"), str)
        or not document["contentVersion"].strip()
    ):
        raise IntegrityError("Metadata contentVersion must be a non-empty string")
    agents = document.get("agents")
    discovery = document.get("discovery")
    if not isinstance(agents, list) or not all(
        isinstance(item, dict) for item in agents
    ):
        raise IntegrityError("Metadata agents must be an array of objects")
    if not isinstance(discovery, dict):
        raise IntegrityError("Metadata discovery must be an object")
    identifiers = [item.get("id") for item in agents]
    if any(not isinstance(item, str) or not item for item in identifiers) or len(
        identifiers
    ) != len(set(identifiers)):
        raise IntegrityError("Metadata agents require unique non-empty identifiers")
    known = set(identifiers)
    creators = discovery.get("creatorAgentIds")
    contributors = discovery.get("contributorAgentIds")
    if not isinstance(creators, list) or not creators:
        raise IntegrityError("Metadata discovery requires at least one creatorAgentId")
    if not isinstance(contributors, list):
        raise IntegrityError("Metadata contributorAgentIds must be an array")
    missing = (set(creators) | set(contributors)) - known
    if missing:
        raise IntegrityError(
            "Metadata discovery references unknown agents: "
            + ", ".join(sorted(missing))
        )
    if discovery.get("resourceType") != "Dataset":
        raise IntegrityError("VAO 0.4 discovery.resourceType must be 'Dataset'")
    for field in ("relatedIdentifiers", "fundingReferences", "subjects"):
        if not isinstance(discovery.get(field), list):
            raise IntegrityError(f"Metadata discovery.{field} must be an array")


def _rewrite_carrier(
    source: Path, target: Path, manifest_raw: bytes, carrier_raw: bytes
) -> None:
    with (
        zipfile.ZipFile(source, "r") as old,
        zipfile.ZipFile(target, "w", allowZip64=True) as new,
    ):
        mime_info = zipfile.ZipInfo("mimetype")
        mime_info.compress_type = zipfile.ZIP_STORED
        mime_info.external_attr = 0o100644 << 16
        new.writestr(mime_info, MIMETYPE)
        for name, raw in ((MANIFEST_NAME, manifest_raw), (CARRIER_NAME, carrier_raw)):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            new.writestr(info, raw)
        for old_info in old.infolist():
            if old_info.filename in {"mimetype", MANIFEST_NAME, CARRIER_NAME}:
                continue
            info = zipfile.ZipInfo(old_info.filename, date_time=old_info.date_time)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = old_info.external_attr
            info.comment = old_info.comment
            if old_info.is_dir():
                new.writestr(info, b"")
                continue
            with (
                old.open(old_info, "r") as input_stream,
                new.open(info, "w", force_zip64=True) as output_stream,
            ):
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
