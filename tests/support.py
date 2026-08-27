from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from typing import Any

from vao_cli.models import PRODUCTION, RemoteFile, ResolvedRecord


def make_vao() -> tuple[bytes, dict[str, Any], bytes]:
    payload = b"tiny acoustic realization\n"
    realization_id = "urn:test:realization:audio"
    asset_id = "urn:test:asset:audio"
    manifest = {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/manifest.json",
        "@context": ["https://w3id.org/modavis/vao/0.4.0/context.jsonld"],
        "type": "VirtualAcousticObject",
        "formatVersion": "0.4.0",
        "id": "urn:test:vao",
        "createdAt": "2026-08-26T00:00:00Z",
        "modifiedAt": "2026-08-26T00:00:00Z",
        "title": {"en": "Tiny VAO"},
        "description": {"en": "Test carrier"},
        "release": {
            "id": "urn:test:release:1",
            "revision": 1,
            "contentVersion": "1.0.0",
        },
        "logicalAssets": [
            {
                "id": asset_id,
                "labels": {"en": "Audio"},
                "roles": ["sample"],
                "realizationIds": [realization_id],
            }
        ],
        "realizations": [
            {
                "id": realization_id,
                "assetId": asset_id,
                "byteSize": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "mediaType": "audio/wav",
                "qualityTier": "bootstrap",
                "representationStatus": "master",
                "technicalMetadata": {"kind": "audio"},
                "chunking": _chunks(payload),
            }
        ],
        "assetGroups": [
            {
                "id": "urn:test:group:bootstrap",
                "type": "AssetGroup",
                "labels": {"en": "Bootstrap"},
                "selectionSetId": "bootstrap",
                "qualityTier": "bootstrap",
                "availability": "offline-required",
                "selectionPolicy": "independent",
                "realizationIds": [realization_id],
                "dependsOnGroupIds": [],
                "totalByteSize": len(payload),
                "requiredCapabilities": [],
                "materializesProfileIds": [],
                "cachePolicy": {"evictable": False, "priority": 100},
            }
        ],
        "discovery": {
            "resourceType": "Dataset",
            "creatorAgentIds": ["urn:test:agent"],
            "contributorAgentIds": [],
            "relatedIdentifiers": [],
            "fundingReferences": [],
            "subjects": [],
        },
        "scientific": {
            "agents": [
                {
                    "id": "urn:test:agent",
                    "agentKind": "person",
                    "labels": {"en": "Test Creator"},
                }
            ],
            "activities": [],
            "observations": [],
            "analyses": [],
            "calibrations": [],
            "protocols": [],
            "softwareEnvironments": [],
            "claims": [],
            "reviews": [],
            "consents": [],
        },
    }
    manifest_raw = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    carrier = {
        "formatVersion": "0.4.0",
        "type": "VAOCarrier",
        "carrierMode": "bootstrap",
        "releaseId": manifest["release"]["id"],
        "manifestByteSize": len(manifest_raw),
        "manifestSHA256": hashlib.sha256(manifest_raw).hexdigest(),
        "completeGroupIds": [],
        "embeddedRealizations": [
            {"realizationId": realization_id, "path": "payload/audio.bin"}
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", allowZip64=True) as archive:
        archive.writestr(
            "mimetype",
            b"application/vnd.modavis.vao+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr(
            "vao-manifest.json", manifest_raw, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "META-INF/vao-carrier.json",
            json.dumps(carrier, sort_keys=True, indent=2).encode() + b"\n",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("payload/audio.bin", payload, compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue(), manifest, payload


def make_pack_vao() -> tuple[bytes, str, bytes]:
    payload = b"selective geometry bytes\n"
    target_id = "urn:test:realization:model"
    pack_id = "urn:test:realization:pack"
    pack_manifest_id = "urn:test:realization:pack-manifest"
    pack_buffer = io.BytesIO()
    with zipfile.ZipFile(pack_buffer, "w", allowZip64=True) as pack:
        pack.writestr("models/low.glb", payload, compress_type=zipfile.ZIP_STORED)
    pack_raw = pack_buffer.getvalue()
    pack_manifest = {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/pack-manifest.json",
        "type": "VAOPackManifest",
        "formatVersion": "0.4.0",
        "id": "urn:test:pack-manifest",
        "releaseId": "urn:test:release:pack:1",
        "members": [
            {
                "realizationId": target_id,
                "path": "models/low.glb",
                "mediaType": "model/gltf-binary",
                "byteSize": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        "rejectUnlistedMembers": True,
    }
    pack_manifest_raw = json.dumps(pack_manifest, sort_keys=True).encode() + b"\n"
    distribution_id = "urn:test:distribution:pack-member"
    manifest = {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/manifest.json",
        "@context": ["https://w3id.org/modavis/vao/0.4.0/context.jsonld"],
        "type": "VirtualAcousticObject",
        "formatVersion": "0.4.0",
        "id": "urn:test:vao:pack",
        "title": {"en": "Pack VAO"},
        "release": {
            "id": "urn:test:release:pack:1",
            "revision": 1,
            "contentVersion": "1.0.0",
        },
        "logicalAssets": [
            {
                "id": "urn:test:asset:model",
                "labels": {"en": "Low model"},
                "realizationIds": [target_id],
            },
            {
                "id": "urn:test:asset:pack",
                "labels": {"en": "Pack"},
                "realizationIds": [pack_id, pack_manifest_id],
            },
        ],
        "realizations": [
            {
                "id": target_id,
                "assetId": "urn:test:asset:model",
                "byteSize": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "mediaType": "model/gltf-binary",
                "qualityTier": "mobile",
                "technicalMetadata": {"kind": "geometry"},
                "distributionIds": [distribution_id],
            },
            {
                "id": pack_id,
                "assetId": "urn:test:asset:pack",
                "byteSize": len(pack_raw),
                "sha256": hashlib.sha256(pack_raw).hexdigest(),
                "mediaType": "application/zip",
                "qualityTier": "custom",
                "technicalMetadata": {"kind": "data"},
            },
            {
                "id": pack_manifest_id,
                "assetId": "urn:test:asset:pack",
                "byteSize": len(pack_manifest_raw),
                "sha256": hashlib.sha256(pack_manifest_raw).hexdigest(),
                "mediaType": "application/json",
                "qualityTier": "custom",
                "technicalMetadata": {"kind": "data"},
            },
        ],
        "distributions": [
            {
                "id": distribution_id,
                "kind": "pack-member",
                "packRealizationId": pack_id,
                "memberPath": "models/low.glb",
                "packManifestSHA256": hashlib.sha256(pack_manifest_raw).hexdigest(),
            }
        ],
        "repositoryBindings": [],
        "assetGroups": [],
    }
    manifest_raw = json.dumps(manifest, sort_keys=True).encode() + b"\n"
    carrier = {
        "formatVersion": "0.4.0",
        "type": "VAOCarrier",
        "carrierMode": "bootstrap",
        "releaseId": manifest["release"]["id"],
        "manifestByteSize": len(manifest_raw),
        "manifestSHA256": hashlib.sha256(manifest_raw).hexdigest(),
        "completeGroupIds": [],
        "embeddedRealizations": [
            {"realizationId": pack_id, "path": "payload/models.zip"},
            {
                "realizationId": pack_manifest_id,
                "path": "payload/vao-pack-manifest.json",
            },
        ],
    }
    root = io.BytesIO()
    with zipfile.ZipFile(root, "w", allowZip64=True) as archive:
        archive.writestr("mimetype", b"application/vnd.modavis.vao+zip")
        archive.writestr("vao-manifest.json", manifest_raw)
        archive.writestr("META-INF/vao-carrier.json", json.dumps(carrier).encode())
        archive.writestr(
            "payload/models.zip", pack_raw, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "payload/vao-pack-manifest.json",
            pack_manifest_raw,
            compress_type=zipfile.ZIP_STORED,
        )
    return root.getvalue(), target_id, payload


def _chunks(payload: bytes) -> dict[str, Any]:
    split = len(payload) // 2
    parts = (payload[:split], payload[split:])
    offset = 0
    values = []
    for index, part in enumerate(parts):
        values.append(
            {
                "index": index,
                "offset": offset,
                "length": len(part),
                "digest": {
                    "algorithm": "sha256",
                    "value": hashlib.sha256(part).hexdigest(),
                },
            }
        )
        offset += len(part)
    return {"strategy": "fixed-size", "chunkSize": split, "chunks": values}


class MemoryResponse(io.BytesIO):
    status = 206

    def __init__(self, data: bytes, start: int, end: int, total: int):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Range"] = f"bytes {start}-{end}/{total}"

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return "https://zenodo.org/api/records/1/files/test/content"


class MemoryHTTP:
    def __init__(self, data: bytes):
        self.data = data
        self.ranges: list[tuple[int, int]] = []

    def get_range(self, _url: str, start: int, end: int):
        self.ranges.append((start, end))
        return self.data[start : end + 1], Message()

    def get_bytes(self, _url: str, *, maximum: int, headers=None):
        if len(self.data) > maximum:
            raise AssertionError("test body exceeds maximum")
        return self.data

    def get_cached_bytes(self, url: str, *, maximum: int, ttl: float = 300):
        return self.get_bytes(url, maximum=maximum)

    @contextmanager
    def open(self, _url: str, *, headers=None):
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", (headers or {}).get("Range", ""))
        if not match:
            response = MemoryResponse(self.data, 0, len(self.data) - 1, len(self.data))
            response.status = 200
        else:
            start, end = int(match.group(1)), int(match.group(2))
            self.ranges.append((start, end))
            response = MemoryResponse(
                self.data[start : end + 1], start, end, len(self.data)
            )
        yield response


class MemoryZenodoClient:
    def __init__(self, data: bytes):
        self.http = MemoryHTTP(data)
        self.instance = PRODUCTION
        self.record = {
            "id": 1,
            "doi": "10.5281/zenodo.1",
            "conceptdoi": "10.5281/zenodo.10",
            "metadata": {
                "title": "Tiny VAO",
                "publication_date": "2026-08-26",
                "version": "1.0.0",
            },
        }

    def resolve(self, doi: str, *, allow_concept: bool = True):
        return ResolvedRecord(
            requested_doi=doi,
            resolved_doi="10.5281/zenodo.1",
            concept_doi="10.5281/zenodo.10",
            record_id="1",
            instance=PRODUCTION,
            record=self.record,
        )

    def for_resolved(self, _resolved):
        return self

    def files(self, _record):
        return [
            RemoteFile(
                "tiny.vao",
                len(self.http.data),
                None,
                "https://zenodo.org/api/records/1/files/test/content",
            )
        ]


def write_vao(path: Path) -> tuple[dict[str, Any], bytes]:
    data, manifest, payload = make_vao()
    path.write_bytes(data)
    return manifest, payload
