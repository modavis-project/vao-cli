from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .cache import PersistentCache
from .catalog import DEFAULT_COMMUNITY
from .errors import VAOCLIError
from .local import find_vao_standard_root
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
    add(
        "VAO 0.5 validator",
        bool(root and (root / "Tools" / "vao05.py").is_file()),
        str(root / "Tools" / "vao05.py") if root else "not found",
    )
    add(
        "VAO 0.5 schemas",
        bool(root and (root / "Schemas" / "vao-manifest-0.5.0.schema.json").is_file()),
        str(root / "Schemas") if root else "not found",
    )
    if root:
        probe = subprocess.run(
            [sys.executable, str(root / "Tools" / "vao05.py"), "--help"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        add(
            "VAO 0.5 validator runtime",
            probe.returncode == 0,
            "runnable"
            if probe.returncode == 0
            else probe.stderr.strip() or probe.stdout.strip(),
        )
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
