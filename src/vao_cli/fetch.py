from __future__ import annotations

import binascii
import hashlib
import os
import tempfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import IntegrityError, ResolutionError, UnsupportedError
from .local import (
    run_reference_descriptor_bytes,
    run_reference_manifest_validator,
    validate_standard_descriptor_schema,
)
from .models import RemoteFile
from .release import find_carrier_record, select_carrier_file
from .remote_zip import RemoteZipEntry, RemoteZipReader
from .selection import SelectionConstraints, choose_one
from .vao import (
    CARRIER_NAME,
    MANIFEST_NAME,
    MIMETYPE,
    RELEASE_NAME,
    basic_manifest_errors,
    strict_json,
    verify_carrier_binding,
)
from .zenodo import ZenodoClient


def fetch_realization(
    client: ZenodoClient,
    doi: str,
    identifier: str | None,
    output: Path,
    *,
    file_key: str | None = None,
    allow_concept: bool = True,
    dry_run: bool = False,
    asset_id: str | None = None,
    group_id: str | None = None,
    kind: str | None = None,
    quality: str | None = None,
    media_type: str | None = None,
    max_bytes: int | None = None,
    capability: str | None = None,
    profile: str | None = None,
    prefer: str = "best",
    chunks: str | None = None,
    conformance: bool = True,
    standard_root: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    resolved = client.resolve(doi, allow_concept=allow_concept)
    client = client.for_resolved(resolved)
    files = client.files(resolved.record)
    release_file = next((item for item in files if item.key == RELEASE_NAME), None)
    release_raw = (
        client.http.get_cached_bytes(release_file.content_url, maximum=16 * 1024 * 1024)
        if release_file
        else None
    )
    release_descriptor = (
        strict_json(release_raw, RELEASE_NAME, maximum=16 * 1024 * 1024)
        if release_raw is not None
        else None
    )
    archive = select_carrier_file(
        files, release_descriptor, file_key=file_key, mode="bootstrap"
    )
    reader = RemoteZipReader(client.http, archive.content_url, archive.size)
    if reader.read(reader.require_entry("mimetype"), maximum=256) != MIMETYPE:
        raise IntegrityError(f"{archive.key!r} is not a VAO carrier")
    manifest_raw = reader.read(
        reader.require_entry(MANIFEST_NAME), maximum=64 * 1024 * 1024
    )
    manifest = strict_json(manifest_raw, f"{archive.key}:{MANIFEST_NAME}")
    errors = basic_manifest_errors(manifest)
    if errors:
        raise IntegrityError(
            "Remote VAO manifest failed basic validation: " + "; ".join(errors[:8])
        )
    conformance_report: dict[str, Any] | None = None
    release_conformance: dict[str, Any] | None = None
    if conformance:
        conformance_report = run_reference_manifest_validator(
            manifest_raw, standard_root=standard_root, required=True
        )
        if not conformance_report["valid"]:
            raise IntegrityError(
                f"Remote manifest failed full VAO {manifest.get('formatVersion')} conformance: "
                + (conformance_report["stderr"] or conformance_report["stdout"])
            )
        if release_raw is not None:
            release_conformance = run_reference_descriptor_bytes(
                "release", release_raw, standard_root=standard_root, required=True
            )
            if not release_conformance["valid"]:
                raise IntegrityError(
                    f"Release descriptor failed full VAO {manifest.get('formatVersion')} conformance: "
                    + (release_conformance["stderr"] or release_conformance["stdout"])
                )
    carrier_raw = reader.read(
        reader.require_entry(CARRIER_NAME), maximum=64 * 1024 * 1024
    )
    carrier = strict_json(carrier_raw, f"{archive.key}:{CARRIER_NAME}")
    carrier_conformance: dict[str, Any] | None = None
    if conformance:
        carrier_conformance = validate_standard_descriptor_schema(
            "carrier", carrier_raw, standard_root=standard_root, required=True
        )
        if not carrier_conformance["valid"]:
            raise IntegrityError(
                f"Remote carrier descriptor failed the VAO {manifest.get('formatVersion')} schema: "
                + "; ".join(carrier_conformance["errors"][:8])
            )
    verify_carrier_binding(manifest_raw, manifest, carrier)
    selection = choose_one(
        manifest,
        SelectionConstraints(
            identifier=identifier,
            asset_id=asset_id,
            group_id=group_id,
            kind=kind,
            quality=quality,
            media_type=media_type,
            max_bytes=max_bytes,
            capability=capability,
            profile=profile,
            prefer=prefer,
        ),
    )
    realization = selection["realization"]
    plan = _locate_realization(
        client,
        files,
        archive,
        reader,
        manifest,
        manifest_raw,
        carrier,
        realization,
        release_descriptor=release_descriptor,
        conformance=conformance,
        standard_root=standard_root,
    )
    plan.update(
        {
            "requestedDOI": resolved.requested_doi,
            "resolvedDOI": resolved.resolved_doi,
            "recordId": resolved.record_id,
            "realizationId": realization["id"],
            "assetId": realization.get("assetId"),
            "label": selection["label"],
            "mediaType": realization.get("mediaType"),
            "qualityTier": realization.get("qualityTier"),
            "output": str(output),
            "dryRun": dry_run,
            "manifestConformance": conformance_report,
            "carrierConformance": carrier_conformance,
            "releaseConformance": release_conformance,
        }
    )
    selected_chunks = _select_chunks(realization, chunks) if chunks else None
    if selected_chunks:
        if plan.get("compression") not in {"stored", "raw"}:
            raise UnsupportedError(
                "Verified chunk fetching requires a stored or repository realization"
            )
        plan["chunks"] = [item["index"] for item in selected_chunks]
        plan["byteSize"] = sum(item["length"] for item in selected_chunks)
        plan["partial"] = True
        plan["rangeStart"] = plan["dataOffset"] + selected_chunks[0]["offset"]
        plan["rangeEnd"] = (
            plan["dataOffset"]
            + selected_chunks[-1]["offset"]
            + selected_chunks[-1]["length"]
            - 1
        )
        plan["ranges"] = [
            {
                "chunk": item["index"],
                "start": plan["dataOffset"] + item["offset"],
                "end": plan["dataOffset"] + item["offset"] + item["length"] - 1,
            }
            for item in selected_chunks
        ]
    public = _public_plan(plan)
    if dry_run:
        return public
    if output.exists():
        raise ResolutionError(f"Realization output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    materializer = plan.get("_client", client)
    if selected_chunks:
        _materialize_chunks(
            materializer,
            str(plan["contentURL"]),
            int(plan["dataOffset"]),
            selected_chunks,
            output,
            progress,
        )
    elif plan["compression"] == "raw":
        _materialize_raw(
            materializer,
            str(plan["contentURL"]),
            int(realization["byteSize"]),
            str(realization["sha256"]),
            output,
            progress,
        )
    else:
        _materialize(
            materializer,
            str(plan["contentURL"]),
            plan["_entry"],
            int(plan["dataOffset"]),
            output,
            realization,
            progress,
        )
    public["outputSHA256"] = _sha256_file(output)
    public["verified"] = True
    public["verification"] = {
        "requestedBytes": "verified",
        "realization": "partial" if selected_chunks else "verified",
        **(
            {"outerPack": "not-fully-read"}
            if plan.get("delivery") == "pack-member"
            else {}
        ),
        **(
            {"outerCarrier": "not-fully-read"}
            if plan.get("delivery") == "carrier-member"
            else {}
        ),
    }
    return public


def _locate_realization(
    client: ZenodoClient,
    root_files: list[RemoteFile],
    archive: RemoteFile,
    reader: RemoteZipReader,
    manifest: dict[str, Any],
    manifest_raw: bytes,
    carrier: dict[str, Any],
    realization: dict[str, Any],
    *,
    release_descriptor: dict[str, Any] | None,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, Any]:
    realization_id = str(realization["id"])
    mappings = _mappings(carrier, realization_id)
    if len(mappings) == 1:
        member_name = mappings[0].get("path")
        if not isinstance(member_name, str):
            raise IntegrityError("Carrier realization mapping has no path")
        entry = reader.require_entry(member_name)
        return _zip_plan(
            archive.content_url,
            archive.key,
            member_name,
            entry,
            reader.data_offset(entry),
            "embedded",
        )
    if len(mappings) > 1:
        raise IntegrityError(
            f"Realization {realization_id!r} is embedded more than once"
        )
    distributions = {
        item.get("id"): item
        for item in manifest.get("distributions", [])
        if isinstance(item, dict)
    }
    candidates = [
        distributions[item]
        for item in realization.get("distributionIds", [])
        if item in distributions
    ]
    for distribution in candidates:
        if distribution.get("kind") == "repository":
            return _repository_plan(client, manifest, distribution, realization)
        if distribution.get("kind") == "pack-member":
            return _pack_plan(
                client,
                root_files,
                archive,
                reader,
                manifest,
                carrier,
                distribution,
                realization,
                conformance=conformance,
                standard_root=standard_root,
            )
        if distribution.get("kind") == "carrier-member":
            if release_descriptor is None:
                raise ResolutionError(
                    "Carrier-member acquisition requires vao-release.json in the repository record"
                )
            return _carrier_member_plan(
                client,
                release_descriptor,
                manifest,
                manifest_raw,
                distribution,
                realization,
                conformance=conformance,
                standard_root=standard_root,
            )
    raise ResolutionError(
        f"Realization {realization_id!r} is neither embedded nor available through a supported public distribution"
    )


def _repository_plan(
    client: ZenodoClient,
    manifest: dict[str, Any],
    distribution: dict[str, Any],
    realization: dict[str, Any],
) -> dict[str, Any]:
    if distribution.get("access") != "public":
        raise ResolutionError(
            f"Repository distribution {distribution.get('id')!r} is {distribution.get('access')!r}, not public"
        )
    persistent = distribution.get("persistentIdentifier")
    if not isinstance(persistent, str):
        raise IntegrityError(
            "Repository distribution has no exact persistent identifier"
        )
    resolved = client.resolve(persistent, allow_concept=False)
    distribution_client = client.for_resolved(resolved)
    bindings = {
        item.get("id"): item
        for item in manifest.get("repositoryBindings", [])
        if isinstance(item, dict)
    }
    binding = bindings.get(distribution.get("repositoryBindingId"))
    if not isinstance(binding, dict):
        raise IntegrityError(
            "Repository distribution has no declared repository binding"
        )
    if (
        binding.get("repositoryType")
        != "https://w3id.org/modavis/vao/repository/zenodo"
    ):
        raise UnsupportedError("Only VAO Zenodo repository bindings are supported")
    if binding.get("instance") != resolved.instance.identity:
        raise IntegrityError(
            "Repository binding instance disagrees with the resolved DOI"
        )
    if (
        binding.get("apiProfile")
        != "https://w3id.org/modavis/vao/repository/zenodo/records-api/1"
    ):
        raise UnsupportedError(
            "The repository binding does not use the supported Zenodo Records API profile"
        )
    if binding.get("resolutionPolicy") != "version-pid-record-file":
        raise IntegrityError(
            "Repository binding does not require exact version/record/file resolution"
        )
    if str(distribution.get("recordIdentifier")) != resolved.record_id:
        raise IntegrityError(
            "Repository distribution recordIdentifier disagrees with Zenodo"
        )
    file_key = distribution.get("fileIdentifier")
    matches = [
        item
        for item in distribution_client.files(resolved.record)
        if item.key == file_key
    ]
    if len(matches) != 1:
        raise ResolutionError(
            f"Repository file {file_key!r} does not resolve exactly once"
        )
    remote = matches[0]
    if remote.size != realization.get("byteSize"):
        raise IntegrityError("Repository file size disagrees with the realization")
    return {
        "delivery": "repository",
        "carrier": None,
        "contentURL": remote.content_url,
        "member": remote.key,
        "byteSize": remote.size,
        "compressedSize": remote.size,
        "compression": "raw",
        "directRangeUsable": True,
        "dataOffset": 0,
        "rangeStart": 0,
        "rangeEnd": max(0, remote.size - 1),
        "distributionId": distribution.get("id"),
        "distributionDOI": resolved.resolved_doi,
        "_client": distribution_client,
    }


def _carrier_member_plan(
    client: ZenodoClient,
    release: dict[str, Any],
    manifest: dict[str, Any],
    manifest_raw: bytes,
    distribution: dict[str, Any],
    realization: dict[str, Any],
    *,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, Any]:
    if distribution.get("access") != "public":
        raise ResolutionError(
            f"Carrier-member distribution {distribution.get('id')!r} is not public"
        )
    persistent = distribution.get("persistentIdentifier")
    carrier_id = distribution.get("carrierId")
    file_key = distribution.get("fileIdentifier")
    record_identifier = str(distribution.get("recordIdentifier"))
    if not all(
        isinstance(item, str) and item for item in (persistent, carrier_id, file_key)
    ):
        raise IntegrityError("Carrier-member distribution lacks exact target identity")

    bindings = {
        item.get("id"): item
        for item in manifest.get("repositoryBindings", [])
        if isinstance(item, dict)
    }
    binding = bindings.get(distribution.get("repositoryBindingId"))
    if not isinstance(binding, dict):
        raise IntegrityError("Carrier-member distribution has no repository binding")
    if (
        binding.get("repositoryType")
        != "https://w3id.org/modavis/vao/repository/zenodo"
    ):
        raise UnsupportedError("Only Zenodo carrier-member distributions are supported")
    if binding.get("resolutionPolicy") != "version-pid-record-file":
        raise IntegrityError("Repository binding does not require exact resolution")
    if (
        binding.get("apiProfile")
        != "https://w3id.org/modavis/vao/repository/zenodo/records-api/1"
    ):
        raise UnsupportedError(
            "The repository binding does not use the supported Zenodo Records API profile"
        )

    resolved = client.resolve(str(persistent), allow_concept=False)
    target_client = client.for_resolved(resolved)
    if binding.get("instance") != resolved.instance.identity:
        raise IntegrityError(
            "Repository binding instance disagrees with the target DOI"
        )
    if str(resolved.record_id) != record_identifier:
        raise IntegrityError("Carrier-member recordIdentifier disagrees with Zenodo")
    _record, inventory = find_carrier_record(
        release,
        carrier_id=str(carrier_id),
        version_pid=str(persistent),
        record_identifier=record_identifier,
        file_identifier=str(file_key),
    )
    matches = [
        item for item in target_client.files(resolved.record) if item.key == file_key
    ]
    if len(matches) != 1:
        raise ResolutionError(
            f"Carrier file {file_key!r} does not resolve exactly once"
        )
    remote = matches[0]
    if remote.size != inventory.get("byteSize"):
        raise IntegrityError("Repository carrier size disagrees with vao-release.json")

    reader = RemoteZipReader(target_client.http, remote.content_url, remote.size)
    if reader.read(reader.require_entry("mimetype"), maximum=256) != MIMETYPE:
        raise IntegrityError(f"{file_key!r} is not a VAO carrier")
    target_manifest_raw = reader.read(
        reader.require_entry(MANIFEST_NAME), maximum=64 * 1024 * 1024
    )
    if target_manifest_raw != manifest_raw:
        raise IntegrityError(
            "Target carrier does not embed the exact bootstrap manifest"
        )
    carrier_raw = reader.read(
        reader.require_entry(CARRIER_NAME), maximum=16 * 1024 * 1024
    )
    if len(carrier_raw) != inventory.get("carrierDescriptorByteSize") or hashlib.sha256(
        carrier_raw
    ).hexdigest() != inventory.get("carrierDescriptorSHA256"):
        raise IntegrityError(
            "Target carrier descriptor disagrees with vao-release.json"
        )
    carrier = strict_json(carrier_raw, f"{file_key}:{CARRIER_NAME}")
    if conformance:
        carrier_report = validate_standard_descriptor_schema(
            "carrier", carrier_raw, standard_root=standard_root, required=True
        )
        if not carrier_report["valid"]:
            raise IntegrityError(
                "Target carrier descriptor failed VAO conformance: "
                + "; ".join(carrier_report["errors"][:8])
            )
    verify_carrier_binding(target_manifest_raw, manifest, carrier)
    comparisons = {
        "id": inventory.get("carrierId"),
        "carrierMode": inventory.get("carrierMode"),
        "manifestSHA256": inventory.get("manifestSHA256"),
        "manifestByteSize": inventory.get("manifestByteSize"),
        "completeGroupIds": inventory.get("completeGroupIds"),
    }
    for field, expected in comparisons.items():
        if carrier.get(field) != expected:
            raise IntegrityError(
                f"Target carrier descriptor {field} disagrees with vao-release.json"
            )
    mappings = _mappings(carrier, str(realization["id"]))
    if len(mappings) != 1 or not isinstance(mappings[0].get("path"), str):
        raise IntegrityError(
            "Target carrier does not map the requested realization exactly once"
        )
    member = str(mappings[0]["path"])
    entry = reader.require_entry(member)
    if entry.uncompressed_size != realization.get("byteSize"):
        raise IntegrityError("Carrier member size disagrees with the realization")
    plan = _zip_plan(
        remote.content_url,
        str(carrier_id),
        member,
        entry,
        reader.data_offset(entry),
        "carrier-member",
    )
    plan.update(
        {
            "distributionId": distribution.get("id"),
            "distributionDOI": resolved.resolved_doi,
            "carrierFile": remote.key,
            "carrierByteSize": remote.size,
            "carrierSHA256": inventory.get("sha256"),
            "carrierDescriptorSHA256": inventory.get("carrierDescriptorSHA256"),
            "_client": target_client,
        }
    )
    return plan


def _pack_plan(
    client: ZenodoClient,
    root_files: list[RemoteFile],
    archive: RemoteFile,
    root_reader: RemoteZipReader,
    manifest: dict[str, Any],
    carrier: dict[str, Any],
    distribution: dict[str, Any],
    realization: dict[str, Any],
    *,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, Any]:
    realizations = {
        item.get("id"): item
        for item in manifest.get("realizations", [])
        if isinstance(item, dict)
    }
    pack = realizations.get(distribution.get("packRealizationId"))
    if not isinstance(pack, dict):
        raise IntegrityError("Pack-member distribution has no outer pack realization")
    pack_size = int(pack.get("byteSize", -1))
    base_offset = 0
    pack_files = root_files
    pack_mappings = _mappings(carrier, str(pack["id"]))
    if len(pack_mappings) == 1:
        pack_entry = root_reader.require_entry(str(pack_mappings[0].get("path")))
        if pack_entry.compression != 0:
            raise UnsupportedError(
                "A nested embedded pack must be ZIP_STORED for remote indexing"
            )
        if pack_entry.uncompressed_size != pack_size:
            raise IntegrityError("Embedded pack size disagrees with its realization")
        base_offset = root_reader.data_offset(pack_entry)
        pack_url = archive.content_url
        pack_reader = RemoteZipReader(
            _OffsetHTTP(client.http, pack_url, base_offset),
            "nested-pack",
            pack_size,
            require_vao_layout=False,
        )
        pack_client = client
    else:
        distributions = {
            item.get("id"): item
            for item in manifest.get("distributions", [])
            if isinstance(item, dict)
        }
        outer = [
            distributions[item]
            for item in pack.get("distributionIds", [])
            if item in distributions and distributions[item].get("kind") == "repository"
        ]
        if len(outer) != 1:
            raise ResolutionError(
                "Outer pack is not embedded and has no unique repository distribution"
            )
        outer_plan = _repository_plan(client, manifest, outer[0], pack)
        pack_client = outer_plan["_client"]
        pack_url = str(outer_plan["contentURL"])
        pack_resolved = pack_client.resolve(
            outer[0]["persistentIdentifier"], allow_concept=False
        )
        pack_files = pack_client.files(pack_resolved.record)
        pack_reader = RemoteZipReader(
            pack_client.http, pack_url, pack_size, require_vao_layout=False
        )
    expected_manifest_sha = str(distribution.get("packManifestSHA256"))
    pack_manifest = _embedded_pack_manifest(
        root_reader,
        manifest,
        carrier,
        expected_manifest_sha,
        conformance=conformance,
        standard_root=standard_root,
    ) or _pack_manifest(
        pack_client,
        pack_reader,
        pack_files,
        expected_manifest_sha,
        conformance=conformance,
        standard_root=standard_root,
    )
    if pack_manifest.get("releaseId") != manifest.get("release", {}).get("id"):
        raise IntegrityError(
            "Pack manifest releaseId disagrees with the root VAO release"
        )
    member_path = distribution.get("memberPath")
    members = [
        item
        for item in pack_manifest.get("members", [])
        if isinstance(item, dict)
        and item.get("realizationId") == realization.get("id")
        and item.get("path") == member_path
    ]
    if len(members) != 1:
        raise IntegrityError(
            "Pack manifest does not bind the requested realization and member path"
        )
    if members[0].get("byteSize") != realization.get("byteSize") or members[0].get(
        "sha256"
    ) != realization.get("sha256"):
        raise IntegrityError("Pack member identity disagrees with the VAO realization")
    entry = pack_reader.require_entry(str(member_path))
    plan = _zip_plan(
        pack_url,
        str(pack.get("id")),
        str(member_path),
        entry,
        base_offset + pack_reader.data_offset(entry),
        "pack-member",
    )
    plan["_client"] = pack_client
    plan.update(
        {
            "distributionId": distribution.get("id"),
            "packRealizationId": pack.get("id"),
            "packManifestSHA256": distribution.get("packManifestSHA256"),
        }
    )
    return plan


def _pack_manifest(
    client: ZenodoClient,
    reader: RemoteZipReader,
    files: list[RemoteFile],
    expected_sha: str,
    *,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, Any]:
    names = {
        "vao-pack-manifest.json",
        "pack-manifest.json",
        "META-INF/vao-pack-manifest.json",
    }
    for entry in reader.entries:
        if entry.name in names and entry.uncompressed_size <= 64 * 1024 * 1024:
            raw = reader.read(entry, maximum=64 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() == expected_sha:
                return _validate_pack_manifest(
                    raw, conformance=conformance, standard_root=standard_root
                )
    for remote in files:
        if (
            remote.size <= 64 * 1024 * 1024
            and "pack" in remote.key.lower()
            and remote.key.lower().endswith(".json")
        ):
            raw = client.http.get_cached_bytes(
                remote.content_url, maximum=64 * 1024 * 1024
            )
            if hashlib.sha256(raw).hexdigest() == expected_sha:
                return _validate_pack_manifest(
                    raw, conformance=conformance, standard_root=standard_root
                )
    raise ResolutionError("The exact pack manifest could not be located")


def _embedded_pack_manifest(
    reader: RemoteZipReader,
    manifest: dict[str, Any],
    carrier: dict[str, Any],
    expected_sha: str,
    *,
    conformance: bool,
    standard_root: Path | None,
) -> dict[str, Any] | None:
    realizations = {
        item.get("id"): item
        for item in manifest.get("realizations", [])
        if isinstance(item, dict)
    }
    for mapping in carrier.get("embeddedRealizations", []):
        if not isinstance(mapping, dict):
            continue
        realization = realizations.get(mapping.get("realizationId"))
        if (
            not isinstance(realization, dict)
            or realization.get("sha256") != expected_sha
        ):
            continue
        if int(realization.get("byteSize", -1)) > 64 * 1024 * 1024:
            raise IntegrityError("Embedded pack manifest exceeds the metadata limit")
        raw = reader.read(
            reader.require_entry(str(mapping.get("path"))), maximum=64 * 1024 * 1024
        )
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise IntegrityError(
                "Embedded pack manifest fails its realization identity"
            )
        return _validate_pack_manifest(
            raw, conformance=conformance, standard_root=standard_root
        )
    return None


def _validate_pack_manifest(
    raw: bytes, *, conformance: bool, standard_root: Path | None
) -> dict[str, Any]:
    value = strict_json(raw, "VAO pack manifest")
    if value.get("type") != "VAOPackManifest" or value.get("formatVersion") not in {
        "0.4.0",
        "0.5.0",
    }:
        raise IntegrityError("Pack manifest is not a supported VAO version")
    if value.get("rejectUnlistedMembers") is not True or not isinstance(
        value.get("members"), list
    ):
        raise IntegrityError("Pack manifest does not reject unlisted members")
    if conformance:
        report = run_reference_descriptor_bytes(
            "pack", raw, standard_root=standard_root, required=True
        )
        if not report["valid"]:
            raise IntegrityError(
                f"Pack manifest failed full VAO {value.get('formatVersion')} conformance: "
                + (report["stderr"] or report["stdout"])
            )
    return value


def _mappings(carrier: dict[str, Any], realization_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in carrier.get("embeddedRealizations", [])
        if isinstance(item, dict) and item.get("realizationId") == realization_id
    ]


def _zip_plan(
    url: str,
    carrier: str,
    member: str,
    entry: RemoteZipEntry,
    offset: int,
    delivery: str,
) -> dict[str, Any]:
    return {
        "delivery": delivery,
        "carrier": carrier,
        "contentURL": url,
        "member": member,
        "byteSize": entry.uncompressed_size,
        "compressedSize": entry.compressed_size,
        "compression": "stored"
        if entry.compression == 0
        else "deflate"
        if entry.compression == 8
        else entry.compression,
        "directRangeUsable": entry.compression == 0,
        "dataOffset": offset,
        "rangeStart": offset,
        "rangeEnd": offset + entry.compressed_size - 1
        if entry.compressed_size
        else offset,
        "_entry": entry,
    }


class _OffsetHTTP:
    def __init__(self, parent, url: str, offset: int):
        self.parent = parent
        self.url = url
        self.offset = offset

    def get_range(self, _url: str, start: int, end: int):
        return self.parent.get_range(self.url, self.offset + start, self.offset + end)


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if not key.startswith("_") and key != "dataOffset"
    }


def _select_chunks(
    realization: dict[str, Any], specification: str
) -> list[dict[str, Any]]:
    chunking = realization.get("chunking")
    values = chunking.get("chunks", []) if isinstance(chunking, dict) else []
    if not values:
        raise ResolutionError(
            "The realization has no inline verified chunks; external streaming indices "
            "are inspectable but not yet an acquisition source"
        )
    ordered = sorted(values, key=lambda item: item["index"])
    offset = 0
    for index, item in enumerate(ordered):
        digest = item.get("digest", {})
        if (
            item.get("index") != index
            or item.get("offset") != offset
            or not isinstance(item.get("length"), int)
            or item["length"] <= 0
            or digest.get("algorithm") not in {"sha256", "sha512"}
            or not isinstance(digest.get("value"), str)
        ):
            raise IntegrityError(
                "Inline chunk table is not consecutive and byte-contiguous"
            )
        offset += item["length"]
    if offset != realization.get("byteSize"):
        raise IntegrityError(
            "Inline chunk table does not cover the exact realization extent"
        )
    try:
        if ":" in specification:
            left, right = specification.split(":", 1)
            start = int(left) if left else 0
            stop = int(right) if right else len(values)
        else:
            start, stop = int(specification), int(specification) + 1
    except ValueError as exc:
        raise ResolutionError("Chunk selection must be INDEX or START:STOP") from exc
    selected = [item for item in ordered if start <= item["index"] < stop]
    if (
        not selected
        or selected[0]["index"] != start
        or selected[-1]["index"] != stop - 1
    ):
        raise ResolutionError(
            "Chunk selection is empty or outside the declared chunk range"
        )
    return selected


def _materialize_chunks(
    client: ZenodoClient,
    url: str,
    data_offset: int,
    chunks: list[dict[str, Any]],
    output: Path,
    progress: Callable[[int, int], None] | None,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        total = sum(int(item["length"]) for item in chunks)
        written = 0
        with temporary.open("wb") as target:
            for chunk in chunks:
                start = data_offset + int(chunk["offset"])
                raw, _headers = client.http.get_range(
                    url, start, start + int(chunk["length"]) - 1
                )
                algorithm = chunk["digest"]["algorithm"]
                if hashlib.new(algorithm, raw).hexdigest() != chunk["digest"]["value"]:
                    raise IntegrityError(
                        f"Chunk {chunk['index']} fails its {algorithm} digest"
                    )
                target.write(raw)
                written += len(raw)
                if progress:
                    progress(written, total)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_raw(
    client: ZenodoClient,
    url: str,
    expected_size: int,
    expected_sha256: str,
    output: Path,
    progress: Callable[[int, int], None] | None,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            client.http.open(
                url, headers={"Accept": "application/octet-stream"}
            ) as response,
            temporary.open("wb") as target,
        ):
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > expected_size:
                    raise IntegrityError(
                        "Repository realization exceeds its declared size"
                    )
                digest.update(block)
                target.write(block)
                if progress:
                    progress(size, expected_size)
            target.flush()
            os.fsync(target.fileno())
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise IntegrityError(
                "Repository realization fails exact size/SHA-256 verification"
            )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize(
    client: ZenodoClient,
    url: str,
    entry: RemoteZipEntry,
    offset: int,
    output: Path,
    realization: dict[str, Any],
    progress: Callable[[int, int], None] | None,
) -> None:
    if entry.compression not in {0, 8}:
        raise UnsupportedError(
            f"ZIP compression method {entry.compression} is unsupported"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    crc = 0
    digest = hashlib.sha256()
    written = 0
    expected_compressed = entry.compressed_size
    try:
        if expected_compressed == 0:
            temporary.write_bytes(b"")
        else:
            end = offset + expected_compressed - 1
            with (
                client.http.open(
                    url,
                    headers={
                        "Range": f"bytes={offset}-{end}",
                        "Accept": "application/octet-stream",
                    },
                ) as response,
                temporary.open("wb") as target,
            ):
                status = getattr(response, "status", response.getcode())
                if status != 206:
                    raise IntegrityError(
                        f"Range endpoint returned HTTP {status}, not 206"
                    )
                remaining = expected_compressed
                decoder = zlib.decompressobj(-15) if entry.compression == 8 else None
                while remaining:
                    block = response.read(min(1024 * 1024, remaining))
                    if not block:
                        raise IntegrityError("Remote realization range ended early")
                    remaining -= len(block)
                    data = decoder.decompress(block) if decoder else block
                    if data:
                        target.write(data)
                        written += len(data)
                        crc = binascii.crc32(data, crc)
                        digest.update(data)
                        if progress:
                            progress(written, entry.uncompressed_size)
                    if written > entry.uncompressed_size:
                        raise IntegrityError(
                            "Remote realization expands beyond its declared size"
                        )
                if response.read(1):
                    raise IntegrityError(
                        "Remote realization range exceeded its declared size"
                    )
                if decoder:
                    tail = decoder.flush()
                    if tail:
                        target.write(tail)
                        written += len(tail)
                        crc = binascii.crc32(tail, crc)
                        digest.update(tail)
                        if progress:
                            progress(written, entry.uncompressed_size)
                    if not decoder.eof:
                        raise IntegrityError(
                            "Remote realization deflate stream is incomplete"
                        )
                target.flush()
                os.fsync(target.fileno())
        if expected_compressed == 0:
            digest = hashlib.sha256(b"")
            written = 0
        if written != entry.uncompressed_size:
            raise IntegrityError(
                f"Realization materialized {written} bytes, expected {entry.uncompressed_size}"
            )
        if crc & 0xFFFFFFFF != entry.crc32:
            raise IntegrityError("Realization ZIP CRC-32 mismatch")
        if digest.hexdigest() != realization.get("sha256"):
            raise IntegrityError("Realization SHA-256 disagrees with the VAO manifest")
        if realization.get("byteSize") != written:
            raise IntegrityError(
                "Realization byte size disagrees with the VAO manifest"
            )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
