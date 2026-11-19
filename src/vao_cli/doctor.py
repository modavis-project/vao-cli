from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import VAO_STANDARD_VERSIONS
from .cache import PersistentCache
from .catalog import DEFAULT_COMMUNITY
from .errors import VAOCLIError
from .local import ensure_reference_validator, find_vao_standard_root
from .zenodo import ZenodoClient


def run_doctor(
    client: ZenodoClient,
    cache: PersistentCache,
    *,
    network: bool = False,
    standard_root: Path | None = None,
) -> dict[str, Any]:
    root = find_vao_standard_root(standard_root)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "required": required})

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    add("Persistent cache", True, str(cache.path), required=False)
    add("VAO standard checkout", root is not None, str(root) if root else "not found")
    if root:
        for version in VAO_STANDARD_VERSIONS:
            name = f"VAO {version} validator"
            try:
                validator_root = ensure_reference_validator(root, version=version)
            except VAOCLIError as exc:
                add(name, False, str(exc))
            else:
                add(name, True, str(validator_root))
        mmic_catalog = root / "Profiles" / "MMIC" / "1.0.0" / "dependencies.json"
        add("MMIC 1.0.0 dependency catalog", mmic_catalog.is_file(), str(mmic_catalog))
    if network:
        try:
            community = client.community(DEFAULT_COMMUNITY)
            add("Zenodo API", True, client.instance.identity)
            add(
                "VAO community",
                community.get("slug") == DEFAULT_COMMUNITY,
                str(community.get("metadata", {}).get("title", DEFAULT_COMMUNITY)),
            )
        except (VAOCLIError, OSError) as exc:
            add("Zenodo API", False, str(exc))
    passed = sum(1 for item in checks if item["ok"])
    required_failures = [item for item in checks if item["required"] and not item["ok"]]
    return {
        "healthy": not required_failures,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "cache": cache.stats(),
    }
