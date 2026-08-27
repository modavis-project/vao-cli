from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .local import validate_local_carrier


def compare_carriers(left: Path, right: Path) -> dict[str, Any]:
    """Compare VAO identities and realization declarations without reading payload bytes."""
    left_report = validate_local_carrier(left, verify_payloads=False)
    right_report = validate_local_carrier(right, verify_payloads=False)
    for path, report in ((left, left_report), (right, right_report)):
        if not report["valid"]:
            raise IntegrityError(
                f"Cannot compare invalid carrier {path}: "
                + "; ".join(report["errors"][:8])
            )
    left_manifest = left_report["manifest"]
    right_manifest = right_report["manifest"]
    left_values = _by_id(left_manifest.get("realizations", []))
    right_values = _by_id(right_manifest.get("realizations", []))
    added = sorted(set(right_values) - set(left_values))
    removed = sorted(set(left_values) - set(right_values))
    shared = sorted(set(left_values) & set(right_values))
    changed = [
        identifier
        for identifier in shared
        if _fingerprint(left_values[identifier])
        != _fingerprint(right_values[identifier])
    ]
    byte_identical = [
        identifier
        for identifier in shared
        if left_values[identifier].get("byteSize")
        == right_values[identifier].get("byteSize")
        and left_values[identifier].get("sha256")
        == right_values[identifier].get("sha256")
    ]
    return {
        "sameVAO": left_manifest.get("id") == right_manifest.get("id"),
        "left": _identity(left, left_manifest),
        "right": _identity(right, right_manifest),
        "realizations": {
            "added": added,
            "removed": removed,
            "changedDeclarations": changed,
            "byteIdentical": byte_identical,
            "unchangedDeclarations": sorted(set(shared) - set(changed)),
        },
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "byteIdentical": len(byte_identical),
        },
    }


def _by_id(values: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["id"]): item
        for item in values
        if isinstance(item, dict) and item.get("id")
    }


def _fingerprint(value: dict[str, Any]) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _identity(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    release = manifest.get("release", {})
    return {
        "path": str(path),
        "vaoId": manifest.get("id"),
        "releaseId": release.get("id"),
        "revision": release.get("revision"),
        "contentVersion": release.get("contentVersion"),
        "realizationCount": len(manifest.get("realizations", [])),
    }
