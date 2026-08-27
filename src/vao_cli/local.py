from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from . import VAO_STANDARD_VERSION
from .errors import ConfigurationError, IntegrityError, ResolutionError
from .vao import (
    CARRIER_NAME,
    MANIFEST_NAME,
    MIMETYPE,
    basic_manifest_errors,
    strict_json,
    verify_carrier_binding,
)

MAX_ENTRIES = 100_000
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_DESCRIPTOR_BYTES = 16 * 1024 * 1024
MAX_ENTRY_BYTES = 1024**4
MAX_TOTAL_BYTES = 4 * 1024**4
MAX_PATH_SEGMENTS = 128
MAX_COMPRESSION_RATIO = 1_000
RATIO_CHECK_MIN_BYTES = 64 * 1024 * 1024


def safe_archive_path(name: str) -> bool:
    path = PurePosixPath(name)
    value = name.removesuffix("/")
    return (
        bool(value)
        and not path.is_absolute()
        and not name.startswith("/")
        and "\\" not in name
        and "\x00" not in name
        and not any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in name
        )
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and len(path.parts) <= MAX_PATH_SEGMENTS
    )


def _portable_path(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _read_bounded(
    archive: zipfile.ZipFile, name: str, maximum: int, label: str
) -> bytes:
    with archive.open(name) as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise IntegrityError(f"{label} exceeds the {maximum}-byte limit")
    return raw


def validate_local_carrier(
    path: Path, *, verify_payloads: bool = True
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    verified_bytes = 0
    manifest: dict[str, Any] | None = None
    carrier: dict[str, Any] | None = None
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_ENTRIES:
                errors.append("Archive exceeds the entry-count safety limit")
            if (
                not infos
                or infos[0].filename != "mimetype"
                or infos[0].compress_type != zipfile.ZIP_STORED
            ):
                errors.append("mimetype must be the first stored ZIP entry")
            normalized: dict[str, str] = {}
            portable: dict[str, str] = {}
            total_bytes = 0
            allowed_exact = {"mimetype", MANIFEST_NAME, CARRIER_NAME}
            for info in infos:
                if not safe_archive_path(info.filename):
                    errors.append(f"Unsafe archive path {info.filename!r}")
                canonical = unicodedata.normalize("NFC", info.filename)
                if canonical in normalized:
                    errors.append(
                        "Archive contains duplicate paths after NFC normalization: "
                        f"{normalized[canonical]!r}, {info.filename!r}"
                    )
                normalized[canonical] = info.filename
                folded = _portable_path(info.filename)
                if folded in portable and portable[folded] != info.filename:
                    errors.append(
                        "Archive contains duplicate paths after NFC/case-fold normalization: "
                        f"{portable[folded]!r}, {info.filename!r}"
                    )
                portable[folded] = info.filename
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    errors.append(
                        f"Archive entry is a symbolic link: {info.filename!r}"
                    )
                elif mode and mode not in {stat.S_IFREG, stat.S_IFDIR}:
                    errors.append(f"Archive entry is a special file: {info.filename!r}")
                if info.flag_bits & 0x1:
                    errors.append(f"Archive entry is encrypted: {info.filename!r}")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    errors.append(
                        f"Unsupported compression method for {info.filename!r}"
                    )
                if info.compress_type == zipfile.ZIP_STORED and (
                    info.file_size != info.compress_size
                ):
                    errors.append(
                        f"Stored entry has inconsistent sizes: {info.filename!r}"
                    )
                if (
                    info.compress_type == zipfile.ZIP_DEFLATED
                    and info.file_size > 0
                    and info.compress_size == 0
                ):
                    errors.append(
                        f"Deflated entry has an impossible zero size: {info.filename!r}"
                    )
                if info.file_size > MAX_ENTRY_BYTES:
                    errors.append(
                        f"Archive entry exceeds the size limit: {info.filename!r}"
                    )
                if (
                    info.file_size >= RATIO_CHECK_MIN_BYTES
                    and info.compress_size
                    and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    errors.append(
                        f"Archive entry exceeds the compression-ratio limit: {info.filename!r}"
                    )
                total_bytes += info.file_size
                if info.is_dir():
                    if not info.filename.startswith("payload/"):
                        errors.append(f"Unknown carrier directory {info.filename!r}")
                elif (
                    info.filename not in allowed_exact
                    and not info.filename.startswith("payload/")
                ):
                    errors.append(f"Unknown carrier entry {info.filename!r}")
            if total_bytes > MAX_TOTAL_BYTES:
                errors.append("Archive exceeds the total uncompressed-size limit")
            missing = allowed_exact - set(names)
            if missing:
                errors.append(
                    "Archive lacks required entries: " + ", ".join(sorted(missing))
                )
            if errors:
                return {
                    "valid": False,
                    "errors": sorted(set(errors)),
                    "warnings": warnings,
                    "manifest": None,
                    "carrier": None,
                    "verifiedPayloadBytes": 0,
                    "validationLevel": "structural",
                }
            if archive.getinfo(MANIFEST_NAME).file_size > MAX_MANIFEST_BYTES:
                raise IntegrityError("Manifest exceeds the metadata size limit")
            if archive.getinfo(CARRIER_NAME).file_size > MAX_DESCRIPTOR_BYTES:
                raise IntegrityError(
                    "Carrier descriptor exceeds the metadata size limit"
                )
            if (
                _read_bounded(archive, "mimetype", len(MIMETYPE), "mimetype")
                != MIMETYPE
            ):
                errors.append("Carrier has incorrect mimetype bytes")
            manifest_raw = _read_bounded(
                archive, MANIFEST_NAME, MAX_MANIFEST_BYTES, MANIFEST_NAME
            )
            carrier_raw = _read_bounded(
                archive, CARRIER_NAME, MAX_DESCRIPTOR_BYTES, CARRIER_NAME
            )
            manifest = strict_json(manifest_raw, MANIFEST_NAME)
            carrier = strict_json(carrier_raw, CARRIER_NAME)
            errors.extend(basic_manifest_errors(manifest))
            if carrier.get("formatVersion") != manifest.get("formatVersion"):
                errors.append("Carrier and manifest formatVersion values disagree")
            if carrier.get("type") != "VAOCarrier":
                errors.append("Carrier descriptor has an incorrect type")
            if manifest.get("formatVersion") == VAO_STANDARD_VERSION and carrier.get(
                "carrierMode"
            ) not in {"bootstrap", "custom", "preservation-closure"}:
                errors.append("Carrier descriptor has an invalid VAO 0.4.0 carrierMode")
            try:
                verify_carrier_binding(manifest_raw, manifest, carrier)
            except IntegrityError as exc:
                errors.append(str(exc))
            realizations = {
                item.get("id"): item
                for item in manifest.get("realizations", [])
                if isinstance(item, dict)
            }
            mappings = [
                item
                for item in carrier.get("embeddedRealizations", [])
                if isinstance(item, dict)
            ]
            mapped_paths = [
                str(item.get("path"))
                for item in mappings
                if isinstance(item.get("path"), str)
            ]
            payload_paths = [
                name
                for name in names
                if name.startswith("payload/") and not name.endswith("/")
            ]
            if {_portable_path(item) for item in mapped_paths} != {
                _portable_path(item) for item in payload_paths
            } or len(mapped_paths) != len(
                {_portable_path(item) for item in mapped_paths}
            ):
                errors.append("Carrier embedded mappings do not equal payload closure")
            embedded_ids: set[str] = set()
            if verify_payloads:
                for mapping in mappings:
                    identifier, member = (
                        mapping.get("realizationId"),
                        mapping.get("path"),
                    )
                    realization = realizations.get(identifier)
                    if not isinstance(realization, dict) or member not in names:
                        errors.append(
                            f"Unresolved embedded realization mapping {identifier!r}"
                        )
                        continue
                    if not isinstance(member, str) or not member.startswith("payload/"):
                        errors.append(f"Unsafe embedded realization path {member!r}")
                        continue
                    declared_size = realization.get("byteSize")
                    if not isinstance(declared_size, int) or declared_size < 0:
                        errors.append(
                            f"Embedded realization {identifier!r} has no valid byteSize"
                        )
                        continue
                    if archive.getinfo(member).file_size != declared_size:
                        errors.append(
                            f"Embedded realization {identifier!r} disagrees with the ZIP size"
                        )
                        continue
                    embedded_ids.add(str(identifier))
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(str(member)) as stream:
                        while size <= declared_size:
                            block = stream.read(
                                min(1024 * 1024, declared_size - size + 1)
                            )
                            if not block:
                                break
                            size += len(block)
                            digest.update(block)
                        if size > declared_size:
                            errors.append(
                                f"Embedded realization {identifier!r} exceeds its declared size"
                            )
                            continue
                    verified_bytes += size
                    if size != realization.get(
                        "byteSize"
                    ) or digest.hexdigest() != realization.get("sha256"):
                        errors.append(
                            f"Embedded realization {identifier!r} fails exact byte verification"
                        )
            else:
                embedded_ids = {str(item.get("realizationId")) for item in mappings}
            if carrier.get(
                "carrierMode"
            ) == "preservation-closure" and embedded_ids != set(realizations):
                errors.append("Preservation-closure carrier omits realizations")
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError, IntegrityError) as exc:
        errors.append(str(exc))
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": warnings,
        "manifest": manifest,
        "carrier": carrier,
        "verifiedPayloadBytes": verified_bytes,
        "validationLevel": "structural",
    }


def extract_local_realization(
    carrier_path: Path, realization_id: str, output: Path
) -> dict[str, Any]:
    """Extract one embedded realization with exact VAO identity verification."""
    report = validate_local_carrier(carrier_path, verify_payloads=False)
    if not report["valid"]:
        raise IntegrityError("Carrier is invalid: " + "; ".join(report["errors"][:8]))
    manifest = report["manifest"]
    carrier = report["carrier"]
    realizations = {
        item.get("id"): item
        for item in manifest.get("realizations", [])
        if isinstance(item, dict)
    }
    realization = realizations.get(realization_id)
    if not isinstance(realization, dict):
        raise ResolutionError(f"No realization has ID {realization_id!r}")
    mappings = [
        item
        for item in carrier.get("embeddedRealizations", [])
        if isinstance(item, dict) and item.get("realizationId") == realization_id
    ]
    if len(mappings) != 1:
        raise ResolutionError(
            "Local extraction currently requires one exact embedded realization mapping"
        )
    if output.exists():
        raise ResolutionError(f"Extraction output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            zipfile.ZipFile(carrier_path) as archive,
            archive.open(str(mappings[0]["path"])) as source,
            temporary.open("wb") as target,
        ):
            for block in iter(lambda: source.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        if size != realization.get("byteSize") or digest.hexdigest() != realization.get(
            "sha256"
        ):
            raise IntegrityError("Extracted realization fails exact byte identity")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "carrier": str(carrier_path),
        "realizationId": realization_id,
        "member": mappings[0]["path"],
        "output": str(output),
        "byteSize": size,
        "sha256": digest.hexdigest(),
        "verified": True,
    }


def find_vao_standard_root(explicit: Path | None = None) -> Path | None:
    for candidate in _root_candidates(explicit, ("VAO_STANDARD_ROOT",)):
        if (
            (candidate / "Tools" / "vao04.py").is_file()
            and (candidate / "Schemas" / "vao-manifest-0.4.0.schema.json").is_file()
            and _standard_version(candidate) == VAO_STANDARD_VERSION
        ):
            return candidate
    return None


def _root_candidates(
    explicit: Path | None, environment_names: tuple[str, ...]
) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    for name in environment_names:
        configured = os.environ.get(name)
        if configured:
            candidates.append(Path(configured))
    here = Path(__file__).resolve()
    candidates.extend(here.parents)
    candidates.append(Path.cwd().resolve())
    candidates.extend(Path.cwd().resolve().parents)
    candidates.extend(candidate / "vao-standard" for candidate in list(candidates))
    result: list[Path] = []
    seen: set[Path] = set()
    for raw_candidate in candidates:
        candidate = raw_candidate.expanduser().resolve()
        if candidate not in seen:
            result.append(candidate)
            seen.add(candidate)
    return result


def _standard_version(root: Path) -> str | None:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def ensure_reference_validator(standard_root: Path | None = None) -> Path:
    root = find_vao_standard_root(standard_root)
    if root is None:
        raise ConfigurationError(
            "VAO Standard 0.4.0 reference tools were not found. Set "
            "VAO_STANDARD_ROOT or pass --standard-root with the released "
            "vao-standard v0.4.0 checkout."
        )
    probe = subprocess.run(
        [sys.executable, str(root / "Tools" / "vao04.py"), "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip() or probe.stdout.strip() or "unknown error"
        raise ConfigurationError(
            "VAO Standard 0.4.0 reference validator is not runnable in the current "
            f"Python environment: {detail}"
        )
    return root


def _reference_report(
    process: subprocess.CompletedProcess[str], command: list[str], root: Path
) -> dict[str, Any]:
    return {
        "valid": process.returncode == 0,
        "status": (
            "conforming"
            if process.returncode == 0
            else "nonconforming"
            if process.returncode == 1
            else "operational-error"
        ),
        "returnCode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
        "command": command,
        "standardVersion": VAO_STANDARD_VERSION,
        "standardRoot": str(root),
    }


def run_reference_validator(
    path: Path, *, standard_root: Path | None = None, required: bool = False
) -> dict[str, Any] | None:
    root = (
        ensure_reference_validator(standard_root)
        if required
        else find_vao_standard_root(standard_root)
    )
    if root is None:
        return None
    command = [
        sys.executable,
        str(root / "Tools" / "vao04.py"),
        "validate",
        str(path.resolve()),
    ]
    process = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return _reference_report(process, command, root)


def run_reference_manifest_validator(
    manifest_raw: bytes, *, standard_root: Path | None = None, required: bool = False
) -> dict[str, Any] | None:
    root = (
        ensure_reference_validator(standard_root)
        if required
        else find_vao_standard_root(standard_root)
    )
    if root is None:
        return None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="vao-remote-manifest-", suffix=".json"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(manifest_raw)
        command = [
            sys.executable,
            str(root / "Tools" / "vao04.py"),
            "validate",
            str(temporary),
        ]
        process = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        report = _reference_report(process, command, root)
        report["validator"] = "VAO 0.4.0 reference validator"
        return report
    finally:
        temporary.unlink(missing_ok=True)


def run_reference_descriptor_validator(
    kind: str,
    path: Path,
    *,
    standard_root: Path | None = None,
    required: bool = False,
) -> dict[str, Any] | None:
    if kind not in {"release", "pack", "receipt", "zenodo-metadata"}:
        raise ValueError(f"Unsupported VAO descriptor kind {kind!r}")
    root = (
        ensure_reference_validator(standard_root)
        if required
        else find_vao_standard_root(standard_root)
    )
    if root is None:
        return None
    command = [
        sys.executable,
        str(root / "Tools" / "vao04.py"),
        "validate-descriptor",
        kind,
        str(path.resolve()),
    ]
    process = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return _reference_report(process, command, root)


def run_reference_descriptor_bytes(
    kind: str,
    raw: bytes,
    *,
    standard_root: Path | None = None,
    required: bool = False,
) -> dict[str, Any] | None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"vao-{kind}-", suffix=".json")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(raw)
        return run_reference_descriptor_validator(
            kind,
            temporary,
            standard_root=standard_root,
            required=required,
        )
    finally:
        temporary.unlink(missing_ok=True)


def validate_standard_descriptor_schema(
    kind: str,
    raw: bytes,
    *,
    standard_root: Path | None = None,
    required: bool = False,
) -> dict[str, Any] | None:
    schema_names = {
        "carrier": "vao-carrier-0.4.0.schema.json",
        "pack": "vao-pack-manifest-0.4.0.schema.json",
    }
    if kind not in schema_names:
        raise ValueError(f"Unsupported VAO schema kind {kind!r}")
    root = (
        ensure_reference_validator(standard_root)
        if required
        else find_vao_standard_root(standard_root)
    )
    if root is None:
        return None
    value = strict_json(raw, f"VAO {kind} descriptor")
    schema_path = root / "Schemas" / schema_names[kind]
    schema = strict_json(
        schema_path.read_bytes(),
        str(schema_path),
        maximum=MAX_DESCRIPTOR_BYTES,
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    details = [
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in errors
    ]
    return {
        "valid": not errors,
        "status": "conforming" if not errors else "nonconforming",
        "errors": details,
        "schema": str(schema_path),
        "standardVersion": VAO_STANDARD_VERSION,
        "standardRoot": str(root),
    }
