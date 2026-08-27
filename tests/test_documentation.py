from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

from vao_cli.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def _parser_paths(
    parser: argparse.ArgumentParser, prefix: str = "vao"
) -> list[tuple[str, argparse.ArgumentParser]]:
    paths = [(prefix, parser)]
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, child in action.choices.items():
            path = f"{prefix} {name}"
            paths.extend(_parser_paths(child, path))
    return paths


def test_github_pages_source_is_complete() -> None:
    required = {
        "_config.yml",
        "index.md",
        "404.md",
        "assets/css/style.scss",
        "getting-started.md",
        "command-reference.md",
        "ARCHITECTURE.md",
        "compatibility.md",
        "resolver-api.md",
        "security.md",
        "publication-preparation.md",
        "Gemfile",
    }
    missing = sorted(item for item in required if not (DOCS / item).is_file())
    assert not missing, f"missing Pages files: {missing}"

    config = (DOCS / "_config.yml").read_text(encoding="utf-8")
    assert "theme: minima" in config
    assert "baseurl: /vao-cli" in config
    assert "relative_links:" in config
    assert "publication-preparation.md" in config


def test_internal_markdown_links_resolve() -> None:
    markdown_files = [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]
    failures: list[str] = []
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_text = unquote(target.split("#", 1)[0])
            if not path_text or "{{" in path_text:
                continue
            target_path = (document.parent / path_text).resolve()
            try:
                target_path.relative_to(ROOT.resolve())
            except ValueError:
                failures.append(
                    f"{document.relative_to(ROOT)} escapes the repository: {target}"
                )
                continue
            if not target_path.exists():
                failures.append(f"{document.relative_to(ROOT)} -> {target}")
    assert not failures, "broken internal links:\n" + "\n".join(failures)


def test_command_reference_covers_every_parser_command() -> None:
    reference = (DOCS / "command-reference.md").read_text(encoding="utf-8")
    parser_paths = _parser_paths(build_parser())
    missing_commands = [path for path, _ in parser_paths[1:] if path not in reference]
    assert not missing_commands, (
        f"commands absent from command reference: {missing_commands}"
    )

    missing_options: list[str] = []
    for path, parser in parser_paths:
        for action in parser._actions:
            if action.dest == "help":
                continue
            for option in action.option_strings:
                if option not in reference:
                    missing_options.append(f"{path}: {option}")
    assert not missing_options, (
        f"options absent from command reference: {missing_options}"
    )
