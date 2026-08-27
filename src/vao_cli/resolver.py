from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import IntegrityError, NetworkError, ResolutionError, UnsupportedError
from .local import (
    run_reference_descriptor_bytes,
    run_reference_manifest_validator,
    validate_standard_descriptor_schema,
)
from .models import RemoteFile, VAOInspection
from .remote_zip import RemoteZipReader
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


class VAOResolver:
    def __init__(self, client: ZenodoClient):
        self.client = client

    def inspect(
        self,
        doi: str,
        *,
        file_key: str | None = None,
        allow_concept: bool = True,
        full_conformance: bool = False,
        community_slug: str | None = None,
        standard_root: Path | None = None,
    ) -> VAOInspection:
        resolved = self.client.resolve(doi, allow_concept=allow_concept)
        client = self.client.for_resolved(resolved)
        files = client.files(resolved.record)
        release_file = next((item for item in files if item.key == RELEASE_NAME), None)
        manifest_file = next(
            (item for item in files if item.key == MANIFEST_NAME), None
        )
        release = (
            self._read_json_file(client, release_file, RELEASE_NAME, 16 * 1024 * 1024)
            if release_file
            else None
        )
        candidates = [item for item in files if item.key.lower().endswith(".vao")]
        selected = self._select_file(candidates, file_key)
        warnings: list[str] = []
        communities: list[dict[str, Any]] = []
        community_status = "unknown"
        conformance: dict[str, Any] | None = None
        manifest: dict[str, Any] | None = None
        source_manifest_raw: bytes | None = None
        standalone_manifest_raw: bytes | None = None
        carrier: dict[str, Any] | None = None
        carrier_raw: bytes | None = None
        archive_entries: list[dict[str, Any]] = []

        # A standalone manifest is the cheapest discovery path. When a carrier
        # was explicitly selected, inspect it too so embedded availability is known.
        if manifest_file:
            raw = client.http.get_cached_bytes(
                manifest_file.content_url, maximum=64 * 1024 * 1024
            )
            source_manifest_raw = raw
            standalone_manifest_raw = raw
            manifest = strict_json(raw, MANIFEST_NAME)
        if selected and (file_key is not None or manifest is None):
            reader = RemoteZipReader(client.http, selected.content_url, selected.size)
            mimetype = reader.read(reader.require_entry("mimetype"), maximum=256)
            if mimetype != MIMETYPE:
                raise IntegrityError(f"{selected.key!r} is not a VAO carrier")
            manifest_raw = reader.read(
                reader.require_entry(MANIFEST_NAME), maximum=64 * 1024 * 1024
            )
            source_manifest_raw = manifest_raw
            carrier_raw = reader.read(
                reader.require_entry(CARRIER_NAME), maximum=64 * 1024 * 1024
            )
            archive_manifest = strict_json(
                manifest_raw, f"{selected.key}:{MANIFEST_NAME}"
            )
            carrier = strict_json(carrier_raw, f"{selected.key}:{CARRIER_NAME}")
            verify_carrier_binding(manifest_raw, archive_manifest, carrier)
            if (
                standalone_manifest_raw is not None
                and manifest_raw != standalone_manifest_raw
            ):
                raise IntegrityError(
                    "Standalone and carrier manifests are not exact byte copies"
                )
            manifest = archive_manifest
            archive_entries = [
                {
                    "name": entry.name,
                    "compression": entry.compression,
                    "compressedSize": entry.compressed_size,
                    "uncompressedSize": entry.uncompressed_size,
                }
                for entry in reader.entries
            ]
        elif selected and manifest is not None:
            warnings.append(
                "A standalone manifest was used for discovery; select --file to inspect carrier population."
            )
        if manifest is None:
            if not candidates:
                warnings.append(
                    "The Zenodo record contains neither vao-manifest.json nor a .vao file."
                )
            elif selected is None:
                warnings.append(
                    "The record contains multiple VAO files; select one with --file."
                )
        else:
            errors = basic_manifest_errors(manifest)
            if errors:
                raise IntegrityError(
                    "Remote VAO manifest failed basic validation: "
                    + "; ".join(errors[:8])
                )
            if full_conformance and manifest.get("formatVersion") != "0.4.0":
                raise UnsupportedError(
                    "Full reference conformance is available only for VAO 0.4.0; "
                    "use --no-conformance only for explicitly limited legacy inspection"
                )
            if full_conformance:
                conformance = run_reference_manifest_validator(
                    source_manifest_raw or b"",
                    standard_root=standard_root,
                    required=True,
                )
                if not conformance["valid"]:
                    raise IntegrityError(
                        "Remote manifest failed full VAO 0.4 conformance: "
                        + (conformance["stderr"] or conformance["stdout"])
                    )
                if carrier_raw is not None:
                    carrier_conformance = validate_standard_descriptor_schema(
                        "carrier",
                        carrier_raw,
                        standard_root=standard_root,
                        required=True,
                    )
                    conformance["carrierDescriptor"] = carrier_conformance
                    if not carrier_conformance["valid"]:
                        raise IntegrityError(
                            "Remote carrier descriptor failed the VAO 0.4.0 schema: "
                            + "; ".join(carrier_conformance["errors"][:8])
                        )
                if release_file is not None:
                    release_conformance = run_reference_descriptor_bytes(
                        "release",
                        client.http.get_cached_bytes(
                            release_file.content_url, maximum=16 * 1024 * 1024
                        ),
                        standard_root=standard_root,
                        required=True,
                    )
                    if not release_conformance["valid"]:
                        raise IntegrityError(
                            "Remote release descriptor failed VAO 0.4.0 validation: "
                            + (
                                release_conformance["stderr"]
                                or release_conformance["stdout"]
                            )
                        )
                    conformance["releaseDescriptor"] = release_conformance
        if community_slug:
            try:
                communities = client.record_communities(resolved)
                slugs = {str(item.get("slug")) for item in communities}
                community_status = (
                    "curated" if community_slug in slugs else "not-listed"
                )
            except (AttributeError, NetworkError):
                warnings.append("Zenodo community membership could not be determined.")
        self._verify_release_inventory(
            release, resolved.record_id, files, manifest_file, selected, warnings
        )
        return VAOInspection(
            resolved=resolved,
            selected_file=selected,
            manifest=manifest,
            carrier=carrier,
            release_descriptor=release,
            archive_entries=archive_entries,
            communities=communities,
            community_status=community_status,
            conformance=conformance,
            warnings=warnings,
        )

    def _read_json_file(
        self, client: ZenodoClient, file: RemoteFile, label: str, maximum: int
    ) -> dict[str, Any]:
        raw = client.http.get_cached_bytes(file.content_url, maximum=maximum)
        return strict_json(raw, label, maximum=maximum)

    @staticmethod
    def _select_file(
        candidates: list[RemoteFile], file_key: str | None
    ) -> RemoteFile | None:
        if file_key:
            matches = [item for item in candidates if item.key == file_key]
            if len(matches) != 1:
                raise ResolutionError(
                    f"VAO file key {file_key!r} does not resolve exactly once"
                )
            return matches[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _verify_release_inventory(
        release: dict[str, Any] | None,
        record_id: str,
        files: list[RemoteFile],
        manifest_file: RemoteFile | None,
        selected: RemoteFile | None,
        warnings: list[str],
    ) -> None:
        if release is None:
            warnings.append(
                "The record has no standalone vao-release.json publication descriptor."
            )
            return
        publication = release.get("publication")
        root = publication.get("rootRecord") if isinstance(publication, dict) else None
        if not isinstance(root, dict):
            raise IntegrityError("vao-release.json has no publication root record")
        if str(root.get("recordIdentifier")) != str(record_id):
            raise IntegrityError(
                "vao-release.json root recordIdentifier disagrees with Zenodo"
            )
        inventory = {
            item.get("fileIdentifier"): item
            for item in root.get("files", [])
            if isinstance(item, dict)
        }
        actual = {item.key: item for item in files}
        for key, item in inventory.items():
            if key not in actual:
                raise IntegrityError(
                    f"vao-release.json inventories missing Zenodo file {key!r}"
                )
            if item.get("byteSize") != actual[key].size:
                raise IntegrityError(
                    f"vao-release.json byte size disagrees for {key!r}"
                )
        for target in (manifest_file, selected):
            if target and target.key not in inventory:
                warnings.append(
                    f"Publication descriptor does not inventory {target.key!r}."
                )
