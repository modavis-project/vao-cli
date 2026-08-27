from __future__ import annotations

import re
from urllib.parse import unquote

from .errors import ResolutionError
from .models import PRODUCTION, SANDBOX, ZenodoInstance

ZENODO_DOI = re.compile(r"^(10\.(?:5281|5072)/zenodo\.)([0-9]+)$", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    result = unquote(value.strip())
    for prefix in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if result.lower().startswith(prefix):
            result = result[len(prefix) :]
            break
    result = result.strip().lower()
    if (
        not result.startswith("10.")
        or "/" not in result
        or any(character.isspace() for character in result)
    ):
        raise ResolutionError(f"Not a DOI: {value!r}")
    return result


def zenodo_record_hint(doi: str) -> tuple[ZenodoInstance, str] | None:
    match = ZENODO_DOI.fullmatch(normalize_doi(doi))
    if not match:
        return None
    instance = PRODUCTION if match.group(1).lower().startswith("10.5281") else SANDBOX
    return instance, match.group(2)
