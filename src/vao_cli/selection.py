from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ResolutionError
from .vao import localized

QUALITY_ORDER = {
    "bootstrap": 0,
    "mobile": 1,
    "production": 2,
    "production-spatial": 3,
    "preservation": 4,
    "custom": 2,
}
QUALITY_ALIASES = {
    "preview": "bootstrap",
    "low": "mobile",
    "medium": "production",
    "high": "production-spatial",
    "full": "preservation",
    "archival": "preservation",
}


@dataclass(frozen=True)
class SelectionConstraints:
    identifier: str | None = None
    asset_id: str | None = None
    group_id: str | None = None
    kind: str | None = None
    quality: str | None = None
    media_type: str | None = None
    max_bytes: int | None = None
    capability: str | None = None
    profile: str | None = None
    prefer: str = "best"


def select_realizations(
    manifest: dict[str, Any], constraints: SelectionConstraints
) -> list[dict[str, Any]]:
    assets = {
        item.get("id"): item
        for item in manifest.get("logicalAssets", [])
        if isinstance(item, dict) and item.get("id")
    }
    realizations = [
        item for item in manifest.get("realizations", []) if isinstance(item, dict)
    ]
    groups = {
        item.get("id"): item
        for item in manifest.get("assetGroups", [])
        if isinstance(item, dict) and item.get("id")
    }
    allowed_ids: set[str] | None = None
    identifier = constraints.identifier
    if identifier:
        direct = [item for item in realizations if item.get("id") == identifier]
        if direct:
            allowed_ids = {identifier}
        elif identifier in assets:
            allowed_ids = set(assets[identifier].get("realizationIds", []))
        elif identifier in groups:
            allowed_ids = set(group_realization_ids(groups, identifier))
        else:
            raise ResolutionError(
                f"No realization, logical asset, or asset group has ID {identifier!r}"
            )
    if constraints.asset_id:
        asset = assets.get(constraints.asset_id)
        if asset is None:
            raise ResolutionError(f"No logical asset has ID {constraints.asset_id!r}")
        ids = set(asset.get("realizationIds", []))
        allowed_ids = ids if allowed_ids is None else allowed_ids & ids
    selected_group: dict[str, Any] | None = None
    if constraints.group_id:
        selected_group = groups.get(constraints.group_id)
        if selected_group is None:
            raise ResolutionError(f"No asset group has ID {constraints.group_id!r}")
        ids = set(group_realization_ids(groups, constraints.group_id))
        allowed_ids = ids if allowed_ids is None else allowed_ids & ids
    quality = _quality(constraints.quality) if constraints.quality else None
    result: list[dict[str, Any]] = []
    for realization in realizations:
        realization_id = realization.get("id")
        if allowed_ids is not None and realization_id not in allowed_ids:
            continue
        if constraints.kind:
            technical = realization.get("technicalMetadata", {})
            if (
                not isinstance(technical, dict)
                or technical.get("kind") != constraints.kind
            ):
                continue
        if quality and realization.get("qualityTier") != quality:
            continue
        media_type = str(realization.get("mediaType", ""))
        if constraints.media_type and not _media_matches(
            media_type, constraints.media_type
        ):
            continue
        size = realization.get("byteSize")
        if constraints.max_bytes is not None and (
            not isinstance(size, int) or size > constraints.max_bytes
        ):
            continue
        asset = assets.get(realization.get("assetId"), {})
        result.append(
            {
                "realization": realization,
                "realizationId": realization_id,
                "assetId": realization.get("assetId"),
                "label": localized(asset.get("labels"), str(realization_id)),
                "kind": (realization.get("technicalMetadata") or {}).get("kind"),
                "quality": realization.get("qualityTier"),
                "mediaType": media_type,
                "byteSize": size,
                "groupId": selected_group.get("id") if selected_group else None,
                "score": _score(realization, constraints.prefer),
            }
        )
    result.sort(key=lambda item: str(item["realizationId"]))
    result.sort(key=lambda item: item["score"], reverse=True)
    if constraints.capability or constraints.profile:
        matching_groups = [
            group
            for group in groups.values()
            if (
                not constraints.capability
                or constraints.capability in group.get("requiredCapabilities", [])
            )
            and (
                not constraints.profile
                or constraints.profile in group.get("materializesProfileIds", [])
            )
        ]
        group_ids = {
            rid
            for group in matching_groups
            for rid in group_realization_ids(groups, str(group["id"]))
        }
        result = [item for item in result if item["realizationId"] in group_ids]
    return result


def choose_one(
    manifest: dict[str, Any], constraints: SelectionConstraints
) -> dict[str, Any]:
    values = select_realizations(manifest, constraints)
    if not values:
        raise ResolutionError(
            "No VAO realization satisfies the requested semantic constraints"
        )
    return values[0]


def _quality(value: str) -> str:
    normalized = QUALITY_ALIASES.get(value.lower(), value.lower())
    if normalized not in QUALITY_ORDER:
        raise ResolutionError(f"Unknown quality tier or alias {value!r}")
    return normalized


def _media_matches(actual: str, requested: str) -> bool:
    return actual == requested or (
        requested.endswith("/*") and actual.startswith(requested[:-1])
    )


def _score(realization: dict[str, Any], preference: str) -> tuple[int, int]:
    size = int(realization.get("byteSize", 0))
    quality = QUALITY_ORDER.get(str(realization.get("qualityTier")), -1)
    if preference == "smallest":
        return (-size, quality)
    return (quality, -size)


def group_realization_ids(groups: dict[str, dict[str, Any]], root: str) -> list[str]:
    result: list[str] = []
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        visited.add(identifier)
        group = groups.get(identifier)
        if group is None:
            raise ResolutionError(
                f"Asset group dependency {identifier!r} is unresolved"
            )
        result.extend(str(item) for item in group.get("realizationIds", []))
        for dependency in group.get("dependsOnGroupIds", []):
            visit(str(dependency))

    visit(root)
    return result


def parse_byte_size(value: str) -> int:
    text = value.strip().lower().replace("_", "")
    suffixes = {
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
        "b": 1,
    }
    for suffix in sorted(suffixes, key=len, reverse=True):
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            try:
                result = Decimal(number) * suffixes[suffix]
                if result != result.to_integral_value() or result < 0:
                    raise ValueError
                return int(result)
            except (InvalidOperation, ValueError) as exc:
                raise ResolutionError(f"Invalid byte size {value!r}") from exc
    try:
        result = int(text)
        if result < 0:
            raise ValueError
        return result
    except ValueError as exc:
        raise ResolutionError(f"Invalid byte size {value!r}") from exc
