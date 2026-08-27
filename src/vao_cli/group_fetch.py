from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import ResolutionError
from .fetch import fetch_realization
from .resolver import VAOResolver
from .selection import (
    SelectionConstraints,
    group_realization_ids,
    select_realizations,
)
from .zenodo import ZenodoClient


def fetch_group(
    client: ZenodoClient,
    doi: str,
    group_id: str,
    destination: Path,
    *,
    file_key: str | None = None,
    allow_concept: bool = True,
    dry_run: bool = False,
    conformance: bool = True,
    standard_root: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    inspection = VAOResolver(client).inspect(
        doi,
        file_key=file_key,
        allow_concept=allow_concept,
        full_conformance=conformance,
        standard_root=standard_root,
    )
    if inspection.manifest is None:
        raise ResolutionError("The resolved record has no inspectable VAO manifest")
    matches = select_realizations(
        inspection.manifest, SelectionConstraints(group_id=group_id)
    )
    groups = {
        item.get("id"): item
        for item in inspection.manifest.get("assetGroups", [])
        if isinstance(item, dict) and item.get("id")
    }
    expected = set(group_realization_ids(groups, group_id))
    actual = {str(item["realizationId"]) for item in matches}
    if actual != expected:
        missing = sorted(expected - actual)
        raise ResolutionError(
            "Asset-group realization closure is incomplete: " + ", ".join(missing)
        )
    if not matches:
        raise ResolutionError(f"Asset group {group_id!r} has no realizations")
    plans: list[dict[str, Any]] = []
    names: set[str] = set()
    exact_doi = inspection.resolved.resolved_doi
    for index, match in enumerate(matches, start=1):
        plan = fetch_realization(
            client,
            exact_doi,
            str(match["realizationId"]),
            Path("unused"),
            file_key=file_key,
            allow_concept=False,
            dry_run=True,
            conformance=conformance,
            standard_root=standard_root,
        )
        basename = Path(str(plan["member"])).name or "realization.bin"
        name = f"{index:03d}-{basename}"
        if name in names:
            raise ResolutionError(f"Group acquisition output collision for {name!r}")
        names.add(name)
        plan["output"] = str(destination / name)
        plans.append(plan)
    result = {
        "requestedDOI": inspection.resolved.requested_doi,
        "resolvedDOI": inspection.resolved.resolved_doi,
        "groupId": group_id,
        "destination": str(destination),
        "realizationCount": len(plans),
        "totalByteSize": sum(int(item["byteSize"]) for item in plans),
        "dryRun": dry_run,
        "realizations": plans,
    }
    if dry_run:
        return result
    if destination.exists():
        raise ResolutionError(f"Group destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        completed: list[dict[str, Any]] = []
        for plan in plans:
            target = temporary / Path(str(plan["output"])).name
            report = fetch_realization(
                client,
                exact_doi,
                str(plan["realizationId"]),
                target,
                file_key=file_key,
                allow_concept=False,
                conformance=conformance,
                standard_root=standard_root,
                progress=progress,
            )
            report["output"] = str(destination / target.name)
            completed.append(report)
        os.replace(temporary, destination)
        result["realizations"] = completed
        result["verified"] = True
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
