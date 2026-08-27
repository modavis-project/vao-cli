from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import IntegrityError, ResolutionError
from .local import (
    run_reference_descriptor_validator,
    run_reference_validator,
    validate_local_carrier,
)
from .vao import MANIFEST_NAME


def prepare_publication(
    carrier_path: Path,
    destination: Path,
    *,
    copy_carrier: bool = False,
    standard_root: Path | None = None,
) -> dict[str, Any]:
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise ResolutionError(
            f"Publication staging directory is not empty: {destination}"
        )
    report = validate_local_carrier(carrier_path, verify_payloads=True)
    if not report["valid"]:
        raise IntegrityError("Carrier is invalid: " + "; ".join(report["errors"][:8]))
    manifest = report["manifest"]
    if not isinstance(manifest, dict) or manifest.get("formatVersion") != "0.4.0":
        raise IntegrityError("Publication preparation requires a VAO 0.4.0 carrier")
    reference = run_reference_validator(
        carrier_path, standard_root=standard_root, required=True
    )
    if not reference["valid"]:
        raise IntegrityError(
            "Carrier failed reference validation: "
            + (reference["stderr"] or reference["stdout"])
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        result = _prepare_publication_files(
            carrier_path,
            destination,
            work,
            report,
            reference,
            copy_carrier=copy_carrier,
            standard_root=standard_root,
        )
        if destination.exists():
            destination.rmdir()
        os.replace(work, destination)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _prepare_publication_files(
    carrier_path: Path,
    destination: Path,
    work: Path,
    report: dict[str, Any],
    reference: dict[str, Any],
    *,
    copy_carrier: bool,
    standard_root: Path | None,
) -> dict[str, Any]:
    manifest = report["manifest"]
    with zipfile.ZipFile(carrier_path) as archive:
        manifest_raw = archive.read(MANIFEST_NAME)
    standalone = work / MANIFEST_NAME
    standalone.write_bytes(manifest_raw)
    carrier_target = work / carrier_path.name
    if copy_carrier:
        shutil.copy2(carrier_path, carrier_target)
        carrier_reference = carrier_target.name
    else:
        carrier_reference = str(carrier_path.resolve())
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    carrier_sha = _sha256_file(carrier_path)
    carrier_record = {
        "fileIdentifier": carrier_path.name,
        "source": carrier_reference,
        "role": "carrier",
        "byteSize": carrier_path.stat().st_size,
        "sha256": carrier_sha,
    }
    embedded_ids = [
        item.get("realizationId")
        for item in report["carrier"].get("embeddedRealizations", [])
        if isinstance(item, dict) and item.get("realizationId")
    ]
    if embedded_ids:
        carrier_record["realizationIds"] = embedded_ids
    manifest_record = _file_record(MANIFEST_NAME, standalone, "manifest")
    manifest_record["source"] = str(destination / MANIFEST_NAME)
    upload_files = [manifest_record, carrier_record]
    release = manifest["release"]
    release_template = {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/release.json",
        "type": "VAORelease",
        "formatVersion": "0.4.0",
        "vaoId": manifest["id"],
        "releaseId": release["id"],
        "revision": release["revision"],
        "contentVersion": release["contentVersion"],
        "publication": {
            "topology": "single-record",
            "rootRecord": {
                "id": "urn:vao:publication:pending-root-record",
                "repositoryType": "https://w3id.org/modavis/vao/repository/zenodo",
                "instance": "https://zenodo.org",
                "versionPersistentIdentifier": "https://doi.org/10.5281/zenodo.PENDING",
                "conceptPersistentIdentifier": "https://doi.org/10.5281/zenodo.PENDING-CONCEPT",
                "recordIdentifier": "PENDING",
                "files": [
                    {key: value for key, value in item.items() if key != "source"}
                    for item in upload_files
                ],
            },
            "familyMembers": [],
        },
    }
    metadata_template = _zenodo_metadata(manifest)
    checksums = f"{manifest_sha}  {MANIFEST_NAME}\n{carrier_sha}  {carrier_path.name}\n"
    (work / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    _write_json(work / "vao-release.template.json", release_template)
    _write_json(work / "zenodo-metadata.template.json", metadata_template)
    descriptor_conformance = {
        "release": run_reference_descriptor_validator(
            "release",
            work / "vao-release.template.json",
            standard_root=standard_root,
            required=True,
        ),
        "zenodoMetadata": run_reference_descriptor_validator(
            "zenodo-metadata",
            work / "zenodo-metadata.template.json",
            standard_root=standard_root,
            required=True,
        ),
    }
    invalid_descriptors = [
        name
        for name, result in descriptor_conformance.items()
        if result is None or not result["valid"]
    ]
    if invalid_descriptors:
        raise IntegrityError(
            "Generated publication templates failed VAO 0.4.0 validation: "
            + ", ".join(invalid_descriptors)
        )
    readiness = {
        "readyForLivePublication": False,
        "reason": "Zenodo version/concept DOI and record identity are intentionally pending.",
        "carrierValid": True,
        "referenceConformance": reference,
        "descriptorConformance": descriptor_conformance,
        "manifestSHA256": manifest_sha,
        "carrierSHA256": carrier_sha,
        "verifiedPayloadBytes": report["verifiedPayloadBytes"],
        "requiredBeforeUpload": [
            "Review creators/contributors, rights, consent/privacy, funding, subjects, and related identifiers.",
            "Reserve or create the Zenodo draft and replace every PENDING identity.",
            "Regenerate and validate vao-release.json against the exact record/file inventory.",
            "Perform remote resolve, inspect, selective fetch, full download, and community-submission tests.",
        ],
        "uploadFiles": upload_files,
    }
    _write_json(work / "publication-readiness.json", readiness)
    return {"destination": str(destination), **readiness}


def _zenodo_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    scientific = manifest.get("scientific", {})
    agents = {
        item.get("id"): item
        for item in scientific.get("agents", [])
        if isinstance(item, dict)
    }
    discovery = manifest.get("discovery", {})

    def person(identifier: str) -> dict[str, Any]:
        agent = agents.get(identifier, {})
        labels = agent.get("labels", {})
        name = labels.get("en") or labels.get("und") or identifier
        result: dict[str, Any] = {"name": name}
        if agent.get("orcid"):
            result["orcid"] = str(agent["orcid"]).removeprefix("https://orcid.org/")
        return result

    title = manifest.get("title", {})
    description = manifest.get("description", {})
    keywords = [
        "Virtual Acoustic Object",
        "VAO 0.4",
        *[
            str(item.get("subject"))
            for item in discovery.get("subjects", [])
            if item.get("subject")
        ],
        "research data",
    ]
    keywords = list(dict.fromkeys(keywords))
    for fallback in ("acoustics", "research data"):
        if len(keywords) >= 3:
            break
        if fallback not in keywords:
            keywords.append(fallback)
    return {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/zenodo-metadata.json",
        "type": "VAOZenodoMetadata",
        "formatVersion": "0.4.0",
        "targetAPIProfile": "https://developers.zenodo.org/#deposition-metadata",
        "releaseId": manifest["release"]["id"],
        "publicationRecordId": "urn:vao:publication:pending-root-record",
        "recordRole": "monolithic-root",
        "metadata": {
            "title": title.get("en") or title.get("und") or manifest["id"],
            "upload_type": "dataset",
            "description": description.get("en")
            or description.get("und")
            or "Virtual Acoustic Object",
            "creators": [person(item) for item in discovery.get("creatorAgentIds", [])],
            "contributors": [
                {**person(item), "type": "Other"}
                for item in discovery.get("contributorAgentIds", [])
            ],
            "publication_date": datetime.now(UTC).date().isoformat(),
            "access_right": "restricted",
            "access_conditions": "Publication is blocked pending explicit rights and access review.",
            "keywords": keywords,
            "related_identifiers": [
                {
                    "identifier": item.get("identifier"),
                    "relation": _datacite_relation(str(item.get("relationType"))),
                    **(
                        {"resource_type": item["resourceType"]}
                        if item.get("resourceType")
                        else {}
                    ),
                }
                for item in discovery.get("relatedIdentifiers", [])
            ],
            "version": manifest.get("release", {}).get("contentVersion"),
            "communities": [{"identifier": "virtual-acoustic-objects"}],
            "notes": "Template only. Review licenses/access and adapt to the current Zenodo metadata API before upload.",
        },
    }


def _file_record(name: str, path: Path, role: str) -> dict[str, Any]:
    return {
        "fileIdentifier": name,
        "source": str(path),
        "role": role,
        "byteSize": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _datacite_relation(value: str) -> str:
    relation = value[:1].lower() + value[1:]
    # This spelling is retained by the VAO 0.4 Zenodo projection schema.
    return "isOriginalFormof" if relation == "isOriginalFormOf" else relation


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
