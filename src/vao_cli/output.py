from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any


def emit_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def human_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "-"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(value)


def table(rows: Iterable[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    values = list(rows)
    if not values:
        return "(none)"
    widths = {
        key: max(len(label), *(len(_cell(row.get(key))) for row in values))
        for key, label in columns
    }
    header = "  ".join(label.ljust(widths[key]) for key, label in columns)
    rule = "  ".join("-" * widths[key] for key, _label in columns)
    body = [
        "  ".join(_cell(row.get(key)).ljust(widths[key]) for key, _label in columns)
        for row in values
    ]
    return "\n".join([header, rule, *body])


def error(message: str) -> None:
    print(f"vao: error: {message}", file=sys.stderr)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value) or "-"
    return str(value).replace("\n", " ")
