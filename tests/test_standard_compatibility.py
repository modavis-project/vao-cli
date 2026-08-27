import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from tests.support import MemoryZenodoClient
from vao_cli.fetch import fetch_realization
from vao_cli.local import (
    find_vao_standard_root,
    run_reference_validator,
    validate_local_carrier,
)
from vao_cli.publication import prepare_publication
from vao_cli.resolver import VAOResolver


def _standard_root() -> Path:
    configured = os.environ.get("VAO_STANDARD_ROOT")
    root = find_vao_standard_root(Path(configured) if configured else None)
    if root is None:
        pytest.skip("released VAO Standard 0.4.0 checkout is unavailable")
    return root


def test_released_standard_fixture_passes_both_validation_layers() -> None:
    root = _standard_root()
    fixture = root / "Fixtures" / "VAO04" / "carriers" / "minimal.vao"
    local = validate_local_carrier(fixture, verify_payloads=True)
    assert local["valid"], local["errors"]
    reference = run_reference_validator(fixture, standard_root=root, required=True)
    assert reference["valid"], reference
    assert reference["standardVersion"] == "0.4.0"


def test_remote_reader_and_materializer_accept_released_fixture() -> None:
    root = _standard_root()
    fixture = root / "Fixtures" / "VAO04" / "carriers" / "minimal.vao"
    data = fixture.read_bytes()
    with zipfile.ZipFile(fixture) as archive:
        manifest = json.loads(archive.read("vao-manifest.json"))
        realization = manifest["realizations"][0]
        mapping = next(
            item
            for item in json.loads(archive.read("META-INF/vao-carrier.json"))[
                "embeddedRealizations"
            ]
            if item["realizationId"] == realization["id"]
        )
        expected = archive.read(mapping["path"])

    client = MemoryZenodoClient(data)
    inspection = VAOResolver(client).inspect(
        "10.5281/zenodo.1",
        full_conformance=True,
        standard_root=root,
    )
    assert inspection.conformance
    assert inspection.conformance["valid"]

    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "realization.bin"
        report = fetch_realization(
            client,
            "10.5281/zenodo.1",
            realization["id"],
            output,
            standard_root=root,
        )
        assert output.read_bytes() == expected
        assert report["verified"] is True
        assert report["manifestConformance"]["valid"] is True
        assert report["carrierConformance"]["valid"] is True


def test_publication_templates_conform_for_released_fixture() -> None:
    root = _standard_root()
    fixture = root / "Fixtures" / "VAO04" / "carriers" / "minimal.vao"
    with tempfile.TemporaryDirectory() as folder:
        destination = Path(folder) / "publication-review"
        report = prepare_publication(
            fixture,
            destination,
            copy_carrier=True,
            standard_root=root,
        )
        assert destination.is_dir()
        assert report["readyForLivePublication"] is False
        assert report["descriptorConformance"]["release"]["valid"] is True
        assert report["descriptorConformance"]["zenodoMetadata"]["valid"] is True
