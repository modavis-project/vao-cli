from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from . import VAO_STANDARD_VERSIONS
from .errors import IntegrityError

MIMETYPE = b"application/vnd.modavis.vao+zip"
MANIFEST_NAME = "vao-manifest.json"
CARRIER_NAME = "META-INF/vao-carrier.json"
RELEASE_NAME = "vao-release.json"
SUPPORTED_FORMATS = {"0.3.3", *VAO_STANDARD_VERSIONS}
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_JSON_DEPTH = 128


def strict_json(
    raw: bytes, label: str, *, maximum: int = 64 * 1024 * 1024
) -> dict[str, Any]:
    if len(raw) > maximum:
        raise IntegrityError(f"{label} exceeds the {maximum}-byte limit")
    duplicates: list[str] = []

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                duplicates.append(str(key))
            result[key] = value
        return result

    def constant(value: str):
        raise ValueError(f"non-finite number {value}")

    def integer(value: str) -> int:
        result = int(value)
        if abs(result) > MAX_SAFE_INTEGER:
            raise ValueError(f"integer {value} exceeds the interoperable JSON range")
        return result

    def binary64(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"number {value} overflows finite binary64")
        try:
            if result == 0.0 and Decimal(value) != 0:
                raise ValueError(f"number {value} underflows finite binary64")
        except InvalidOperation as exc:
            raise ValueError(f"invalid number {value}") from exc
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=constant,
            parse_int=integer,
            parse_float=binary64,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise IntegrityError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if duplicates:
        raise IntegrityError(
            f"{label} contains duplicate properties: {', '.join(sorted(set(duplicates)))}"
        )
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} root is not an object")
    _validate_json_value(value, label)
    return value


def _validate_json_value(value: Any, label: str, *, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise IntegrityError(
            f"{label} exceeds the {MAX_JSON_DEPTH}-level JSON depth limit"
        )
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise IntegrityError(f"{label} contains non-scalar Unicode")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, label, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_json_value(key, label, depth=depth + 1)
            _validate_json_value(item, label, depth=depth + 1)


def json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )


def basic_manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = manifest.get("formatVersion")
    if version not in SUPPORTED_FORMATS:
        errors.append(f"Unsupported VAO formatVersion {version!r}")
    for field in (
        "id",
        "release",
        "title",
        "logicalAssets",
        "realizations",
        "assetGroups",
    ):
        if field not in manifest:
            errors.append(f"Manifest lacks required field {field!r}")
    release = manifest.get("release")
    if not isinstance(release, dict) or not release.get("id"):
        errors.append("Manifest release has no identifier")
    if version in VAO_STANDARD_VERSIONS:
        if manifest.get("$schema") != (
            f"https://w3id.org/modavis/vao/{version}/schema/manifest.json"
        ):
            errors.append(f"VAO {version} manifest has an incorrect $schema identifier")
        context = manifest.get("@context")
        if (
            not isinstance(context, list)
            or not context
            or context[0] != (f"https://w3id.org/modavis/vao/{version}/context.jsonld")
        ):
            errors.append(f"VAO {version} manifest lacks the canonical first @context")
        if manifest.get("type") != "VirtualAcousticObject":
            errors.append(f"VAO {version} manifest has an incorrect type")
    assets = (
        manifest.get("logicalAssets")
        if isinstance(manifest.get("logicalAssets"), list)
        else []
    )
    realizations = (
        manifest.get("realizations")
        if isinstance(manifest.get("realizations"), list)
        else []
    )
    identifiers = [
        item.get("id") for item in assets + realizations if isinstance(item, dict)
    ]
    duplicates = [
        item for item, count in Counter(identifiers).items() if item and count > 1
    ]
    if duplicates:
        errors.append(
            "Duplicate asset/realization identifiers: " + ", ".join(sorted(duplicates))
        )
    asset_by_id = {item.get("id"): item for item in assets if isinstance(item, dict)}
    realization_by_id = {
        item.get("id"): item for item in realizations if isinstance(item, dict)
    }
    for realization in realizations:
        if not isinstance(realization, dict):
            errors.append("Realization registry contains a non-object")
            continue
        if realization.get("assetId") not in asset_by_id:
            errors.append(
                f"Realization {realization.get('id')!r} has an unresolved assetId"
            )
        sha = realization.get("sha256")
        if (
            not isinstance(sha, str)
            or len(sha) != 64
            or any(char not in "0123456789abcdef" for char in sha)
        ):
            errors.append(
                f"Realization {realization.get('id')!r} has an invalid SHA-256"
            )
    for asset in assets:
        if not isinstance(asset, dict):
            errors.append("Logical asset registry contains a non-object")
            continue
        for reference in asset.get("realizationIds", []):
            if reference not in realization_by_id:
                errors.append(
                    f"Logical asset {asset.get('id')!r} has unresolved realization {reference!r}"
                )
    return errors


def verify_carrier_binding(
    manifest_raw: bytes, manifest: dict[str, Any], carrier: dict[str, Any]
) -> None:
    expected = hashlib.sha256(manifest_raw).hexdigest()
    if carrier.get("manifestSHA256") != expected or carrier.get(
        "manifestByteSize"
    ) != len(manifest_raw):
        raise IntegrityError("Carrier does not pin the exact manifest bytes")
    release_id = (
        manifest.get("release", {}).get("id")
        if isinstance(manifest.get("release"), dict)
        else None
    )
    if carrier.get("releaseId") != release_id:
        raise IntegrityError("Carrier releaseId does not match manifest release.id")


def localized(value: Any, fallback: str = "") -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return fallback
    for key in ("en", "und"):
        if value.get(key):
            return str(value[key])
    return str(next(iter(value.values()))) if value else fallback


def manifest_summary(
    manifest: dict[str, Any], carrier: dict[str, Any] | None = None
) -> dict[str, Any]:
    realizations = [
        item for item in manifest.get("realizations", []) if isinstance(item, dict)
    ]
    assets = [
        item for item in manifest.get("logicalAssets", []) if isinstance(item, dict)
    ]
    groups = [
        item for item in manifest.get("assetGroups", []) if isinstance(item, dict)
    ]
    embedded = {
        item.get("realizationId")
        for item in (carrier or {}).get("embeddedRealizations", [])
        if isinstance(item, dict)
    }
    modalities = sorted(
        {
            str(item.get("technicalMetadata", {}).get("kind"))
            for item in realizations
            if isinstance(item.get("technicalMetadata"), dict)
            and item.get("technicalMetadata", {}).get("kind")
        }
    )
    tracks = (
        manifest.get("multimodal", {}).get("tracks", [])
        if isinstance(manifest.get("multimodal"), dict)
        else []
    )
    modalities = sorted(
        set(modalities)
        | {
            str(item.get("modality"))
            for item in tracks
            if isinstance(item, dict) and item.get("modality")
        }
    )
    release = (
        manifest.get("release") if isinstance(manifest.get("release"), dict) else {}
    )
    profile_ids = [
        str(item.get("id"))
        for item in manifest.get("profiles", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {
        "id": manifest.get("id"),
        "title": localized(manifest.get("title"), str(manifest.get("id", ""))),
        "description": localized(manifest.get("description")),
        "formatVersion": manifest.get("formatVersion"),
        "releaseId": release.get("id"),
        "revision": release.get("revision"),
        "contentVersion": release.get("contentVersion"),
        "createdAt": manifest.get("createdAt"),
        "modifiedAt": manifest.get("modifiedAt"),
        "modalities": modalities,
        "profiles": profile_ids,
        "logicalAssetCount": len(assets),
        "realizationCount": len(realizations),
        "assetGroupCount": len(groups),
        "totalRealizationBytes": sum(
            int(item.get("byteSize", 0)) for item in realizations
        ),
        "embeddedRealizationCount": len(embedded),
        "materializableProfileCount": len(manifest.get("materializableProfiles", [])),
        "carrierMode": (carrier or {}).get("carrierMode"),
    }


def asset_rows(
    manifest: dict[str, Any], carrier: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    realizations = {
        item.get("id"): item
        for item in manifest.get("realizations", [])
        if isinstance(item, dict)
    }
    embedded = {
        item.get("realizationId")
        for item in (carrier or {}).get("embeddedRealizations", [])
        if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    for asset in manifest.get("logicalAssets", []):
        if not isinstance(asset, dict):
            continue
        for realization_id in asset.get("realizationIds", []):
            realization = realizations.get(realization_id, {})
            technical = (
                realization.get("technicalMetadata", {})
                if isinstance(realization, dict)
                else {}
            )
            result.append(
                {
                    "assetId": asset.get("id"),
                    "label": localized(asset.get("labels"), str(asset.get("id", ""))),
                    "roles": list(asset.get("roles", [])),
                    "realizationId": realization_id,
                    "kind": technical.get("kind")
                    if isinstance(technical, dict)
                    else None,
                    "quality": realization.get("qualityTier")
                    if isinstance(realization, dict)
                    else None,
                    "lod": technical.get("lod")
                    if isinstance(technical, dict)
                    else None,
                    "mediaType": realization.get("mediaType")
                    if isinstance(realization, dict)
                    else None,
                    "byteSize": realization.get("byteSize")
                    if isinstance(realization, dict)
                    else None,
                    "representationStatus": realization.get("representationStatus")
                    if isinstance(realization, dict)
                    else None,
                    "embedded": realization_id in embedded,
                }
            )
    return result


def group_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "label": localized(item.get("labels"), str(item.get("id", ""))),
            "quality": item.get("qualityTier"),
            "availability": item.get("availability"),
            "selectionSet": item.get("selectionSetId"),
            "selectionPolicy": item.get("selectionPolicy"),
            "byteSize": item.get("totalByteSize"),
            "realizationCount": len(item.get("realizationIds", [])),
            "dependencies": list(item.get("dependsOnGroupIds", [])),
            "capabilities": list(item.get("requiredCapabilities", [])),
        }
        for item in manifest.get("assetGroups", [])
        if isinstance(item, dict)
    ]
