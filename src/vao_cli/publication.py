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
    carrier_path: Path | list[Path],
    destination: Path,
    *,
    copy_carrier: bool = False,
    readme: Path | None = None,
    standard_root: Path | None = None,
) -> dict[str, Any]:
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise ResolutionError(
            f"Publication staging directory is not empty: {destination}"
        )
    carriers = [carrier_path] if isinstance(carrier_path, Path) else carrier_path
    if not carriers:
        raise ResolutionError("Publication preparation requires at least one carrier")
    prepared: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    manifest_raw: bytes | None = None
    for path in carriers:
        report = validate_local_carrier(path, verify_payloads=True)
        if not report["valid"]:
            raise IntegrityError(
                f"Carrier {path} is invalid: " + "; ".join(report["errors"][:8])
            )
        manifest = report["manifest"]
        if not isinstance(manifest, dict) or manifest.get("formatVersion") not in {
            "0.4.0",
            "0.5.0",
        }:
            raise IntegrityError("Publication preparation requires VAO 0.4 or 0.5")
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(MANIFEST_NAME)
        if manifest_raw is None:
            manifest_raw = raw
        elif raw != manifest_raw:
            raise IntegrityError(
                "Publication carriers do not embed identical manifests"
            )
        reference = run_reference_validator(
            path, standard_root=standard_root, required=True
        )
        if not reference["valid"]:
            raise IntegrityError(
                f"Carrier {path} failed reference validation: "
                + (reference["stderr"] or reference["stdout"])
            )
        prepared.append((path, report, reference))
    assert manifest_raw is not None
    manifest = prepared[0][1]["manifest"]
    version = str(manifest["formatVersion"])
    if any(item[1]["manifest"]["formatVersion"] != version for item in prepared):
        raise IntegrityError("Publication carriers use different VAO versions")
    modes = [str(item[1]["carrier"].get("carrierMode")) for item in prepared]
    if len(modes) != len(set(modes)):
        raise IntegrityError("Publication carrier modes must be unique")
    if version == "0.5.0" and set(modes) != {
        "bootstrap",
        "preservation-closure",
    }:
        raise IntegrityError(
            "The VAO 0.5 Zenodo profile requires one bootstrap and one preservation-closure carrier"
        )
    if version == "0.4.0" and len(prepared) != 1:
        raise IntegrityError("The legacy VAO 0.4 staging path accepts one carrier")
    if readme is not None and (not readme.is_file() or readme.suffix.lower() != ".pdf"):
        raise ResolutionError("--readme must name an existing PDF file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        result = _prepare_publication_files(
            prepared,
            manifest_raw,
            destination,
            work,
            copy_carrier=copy_carrier,
            readme=readme,
            standard_root=standard_root,
        )
        if destination.exists():
            destination.rmdir()
        os.replace(work, destination)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _prepare_publication_files(
    prepared: list[tuple[Path, dict[str, Any], dict[str, Any]]],
    manifest_raw: bytes,
    destination: Path,
    work: Path,
    *,
    copy_carrier: bool,
    readme: Path | None,
    standard_root: Path | None,
) -> dict[str, Any]:
    manifest = prepared[0][1]["manifest"]
    version = str(manifest["formatVersion"])
    standalone = work / MANIFEST_NAME
    standalone.write_bytes(manifest_raw)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    carrier_records: list[dict[str, Any]] = []
    carrier_hashes: dict[str, str] = {}
    for path, report, _reference in prepared:
        target = work / path.name
        if copy_carrier:
            shutil.copy2(path, target)
            source = str(destination / path.name)
        else:
            source = str(path.resolve())
        with zipfile.ZipFile(path) as archive:
            descriptor_raw = archive.read("META-INF/vao-carrier.json")
        carrier = report["carrier"]
        record: dict[str, Any] = {
            "fileIdentifier": path.name,
            "source": source,
            "role": "carrier",
            "byteSize": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        if version == "0.5.0":
            record.update(
                {
                    "carrierId": carrier["id"],
                    "carrierMode": carrier["carrierMode"],
                    "manifestSHA256": carrier["manifestSHA256"],
                    "manifestByteSize": carrier["manifestByteSize"],
                    "carrierDescriptorSHA256": hashlib.sha256(
                        descriptor_raw
                    ).hexdigest(),
                    "carrierDescriptorByteSize": len(descriptor_raw),
                    "completeGroupIds": carrier.get("completeGroupIds", []),
                }
            )
        else:
            embedded_ids = [
                item.get("realizationId")
                for item in carrier.get("embeddedRealizations", [])
                if isinstance(item, dict) and item.get("realizationId")
            ]
            if embedded_ids:
                record["realizationIds"] = embedded_ids
        carrier_records.append(record)
        carrier_hashes[path.name] = str(record["sha256"])
    manifest_record = _file_record(MANIFEST_NAME, standalone, "manifest")
    manifest_record["source"] = str(destination / MANIFEST_NAME)
    upload_files = [manifest_record, *carrier_records]
    if readme is not None:
        readme_target = work / "README.pdf"
        shutil.copy2(readme, readme_target)
        readme_record = _file_record("README.pdf", readme_target, "documentation")
        readme_record["source"] = str(destination / "README.pdf")
        upload_files.append(readme_record)
    checksum_lines = [
        f"{item['sha256']}  {item['fileIdentifier']}"
        for item in sorted(upload_files, key=lambda value: str(value["fileIdentifier"]))
    ]
    checksums_path = work / "SHA256SUMS"
    checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    checksums_record = _file_record("SHA256SUMS", checksums_path, "checksum")
    checksums_record["source"] = str(destination / "SHA256SUMS")
    upload_files.append(checksums_record)
    release = manifest["release"]
    release_template = {
        "$schema": f"https://w3id.org/modavis/vao/{version}/schema/release.json",
        "type": "VAORelease",
        "formatVersion": version,
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
    metadata_template = _zenodo_metadata(manifest, version)
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
            f"Generated publication templates failed VAO {version} validation: "
            + ", ".join(invalid_descriptors)
        )
    readiness = {
        "readyForLivePublication": False,
        "reason": "Zenodo version/concept DOI and record identity are intentionally pending.",
        "carrierValid": True,
        "referenceConformance": {
            path.name: reference for path, _report, reference in prepared
        },
        "descriptorConformance": descriptor_conformance,
        "manifestSHA256": manifest_sha,
        "carrierSHA256": next(iter(carrier_hashes.values()))
        if len(carrier_hashes) == 1
        else None,
        "carrierSHA256s": carrier_hashes,
        "verifiedPayloadBytes": sum(
            int(report["verifiedPayloadBytes"])
            for _path, report, _reference in prepared
        ),
        "requiredBeforeUpload": [
            "Review creators/contributors, rights, consent/privacy, funding, subjects, acknowledgments, and related identifiers.",
            "Reserve or create the Zenodo draft and replace every PENDING identity.",
            "Ensure carrier-member distributions use the reserved version DOI, record ID, file name, and carrier ID.",
            "Regenerate and validate vao-release.json against the exact record/file inventory.",
            "Perform remote resolve, inspect, selective fetch, full download, and community-submission tests.",
        ],
        "uploadFiles": upload_files,
    }
    _write_json(work / "publication-readiness.json", readiness)
    return {"destination": str(destination), **readiness}


def _zenodo_metadata(manifest: dict[str, Any], version: str) -> dict[str, Any]:
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
        f"VAO {version.removesuffix('.0')}",
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
        "$schema": f"https://w3id.org/modavis/vao/{version}/schema/zenodo-metadata.json",
        "type": "VAOZenodoMetadata",
        "formatVersion": version,
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
