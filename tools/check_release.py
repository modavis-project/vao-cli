#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".jekyll-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "vao_cli.egg-info",
    "_site",
    "_vendor",
}
REQUIRED_FILES = {
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/dependabot.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/release.yml",
    ".github/workflows/validate.yml",
    "AUTHORS.md",
    "CHANGELOG.md",
    "CITATION.cff",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MANIFEST.in",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "VERSION",
    "codemeta.json",
    "docs/_config.yml",
    "docs/index.md",
    "pyproject.toml",
    "requirements-lock.txt",
    "src/vao_cli/cli.py",
    "tests/test_standard_compatibility.py",
}
FORBIDDEN_PATH_PATTERNS = {
    "local macOS path": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "local Linux path": re.compile(r"/home/[A-Za-z0-9._-]+/"),
}
TEXT_SUFFIXES = {
    "",
    ".cff",
    ".css",
    ".json",
    ".md",
    ".py",
    ".scss",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}


def run(*command: str, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def public_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        result.append(path)
    return sorted(result)


def check_metadata() -> str:
    missing = sorted(item for item in REQUIRED_FILES if not (ROOT / item).is_file())
    if missing:
        raise SystemExit("Missing release files: " + ", ".join(missing))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    if project["version"] != version:
        raise SystemExit("VERSION and pyproject.toml disagree")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    if f'version: "{version}"' not in citation:
        raise SystemExit("CITATION.cff does not declare the package version")
    codemeta = json.loads((ROOT / "codemeta.json").read_text(encoding="utf-8"))
    if codemeta.get("version") != version:
        raise SystemExit("codemeta.json does not declare the package version")
    if (ROOT / "src/vao_cli/integrations.py").exists():
        raise SystemExit("The private application adapter must not be published")
    return version


def check_public_text() -> None:
    failures: list[str] = []
    for path in public_files():
        if path == Path(__file__).resolve():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_PATH_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{path.relative_to(ROOT)}: {label}")
    if failures:
        raise SystemExit("Public-surface check failed:\n" + "\n".join(failures))


def check_archive(path: Path, version: str) -> None:
    unsafe = re.compile(r"(^|/)(?:\.\.?|__pycache__|\.pytest_cache|\.ruff_cache)(/|$)")
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path, mode="r:gz") as archive:
            names = archive.getnames()
    if not names or any(
        name.startswith(("/", "\\")) or unsafe.search(name) for name in names
    ):
        raise SystemExit(f"Unsafe or development-only path in {path.name}")
    expected = f"vao_cli-{version}.dist-info/METADATA"
    if path.suffix == ".whl" and expected not in names:
        raise SystemExit(f"Wheel lacks {expected}")


def build_and_smoke_test(version: str) -> None:
    with tempfile.TemporaryDirectory(prefix="vao-cli-release-") as folder:
        temporary = Path(folder)
        dist = temporary / "dist"
        run(sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist))
        artifacts = sorted(dist.iterdir())
        wheels = [item for item in artifacts if item.suffix == ".whl"]
        sdists = [item for item in artifacts if item.name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            raise SystemExit(
                "Build must produce exactly one wheel and one source archive"
            )
        run(sys.executable, "-m", "twine", "check", *(str(item) for item in artifacts))
        for artifact in artifacts:
            check_archive(artifact, version)

        environment = temporary / "smoke"
        run(sys.executable, "-m", "venv", str(environment))
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        run(str(python), "-m", "pip", "install", str(wheels[0]))
        executable = environment / ("Scripts/vao.exe" if os.name == "nt" else "bin/vao")
        output = subprocess.check_output([str(executable), "--version"], text=True)
        if version not in output:
            raise SystemExit("Installed console command reports the wrong version")
        run(str(executable), "--help")


def check_standard_fixture(environment: dict[str, str]) -> None:
    from vao_cli.local import ensure_reference_validator

    root = ensure_reference_validator()
    fixture = root / "Fixtures" / "VAO04" / "carriers" / "minimal.vao"
    if not fixture.is_file():
        raise SystemExit("Released VAO 0.4.0 minimal carrier fixture is missing")
    command = [
        sys.executable,
        "-m",
        "vao_cli",
        "--json",
        "validate",
        str(fixture),
    ]
    print("+", " ".join(command), flush=True)
    process = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        sys.stderr.write(process.stdout)
        sys.stderr.write(process.stderr)
        raise SystemExit("Published VAO 0.4.0 fixture validation failed")
    report = json.loads(process.stdout)
    if not report.get("valid") or not report.get("referenceConformance", {}).get(
        "valid"
    ):
        raise SystemExit("Published VAO 0.4.0 fixture lacks full conformance")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the VAO CLI release gate")
    parser.add_argument("--skip-build", action="store_true")
    arguments = parser.parse_args()

    version = check_metadata()
    check_public_text()
    environment = os.environ.copy()
    run(sys.executable, "-m", "ruff", "check", ".", env=environment)
    run(sys.executable, "-m", "ruff", "format", "--check", ".", env=environment)
    run(sys.executable, "-m", "pytest", "-q", env=environment)
    check_standard_fixture(environment)
    if not arguments.skip_build:
        build_and_smoke_test(version)
    print(f"VAO CLI {version} release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
