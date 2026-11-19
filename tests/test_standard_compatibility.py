import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from tests.support import MemoryZenodoClient, MultiFileMemoryZenodoClient
from vao_cli.cli import main
from vao_cli.errors import IntegrityError
from vao_cli.fetch import fetch_realization
from vao_cli.local import (
    find_vao_standard_root,
    run_reference_validator,
    validate_local_carrier,
)
from vao_cli.materialize import materialize_carrier
from vao_cli.publication import prepare_publication
from vao_cli.resolver import VAOResolver
from vao_cli.vao import CARRIER_NAME, MANIFEST_NAME, RELEASE_NAME, json_bytes


def _standard_root(version: str = "0.4.0") -> Path:
    configured = os.environ.get("VAO_STANDARD_ROOT")
    root = find_vao_standard_root(
        Path(configured) if configured else None, version=version
    )
    if root is None:
        pytest.skip(f"released VAO Standard {version} checkout is unavailable")
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


def test_custom_carrier_conforms_to_released_vao_05(tmp_path: Path) -> None:
    root = _standard_root("0.5.0")
    fixture = root / "Fixtures" / "VAO05" / "carriers" / "minimal.vao"
    with zipfile.ZipFile(fixture) as archive:
        manifest_bytes = archive.read("vao-manifest.json")
        manifest = json.loads(manifest_bytes)
    output = tmp_path / "custom.vao"
    report = materialize_carrier(
        MemoryZenodoClient(fixture.read_bytes()),
        "10.5281/zenodo.1",
        output,
        realization_ids=[manifest["realizations"][0]["id"]],
        standard_root=root,
    )
    assert report["verified"] is True
    assert report["referenceConformance"]["valid"] is True
    with zipfile.ZipFile(output) as archive:
        assert archive.read("vao-manifest.json") == manifest_bytes
        descriptor = json.loads(archive.read("META-INF/vao-carrier.json"))
    assert (
        descriptor["$schema"]
        == "https://w3id.org/modavis/vao/0.5.0/schema/carrier.json"
    )
    assert descriptor["carrierMode"] == "custom"


def test_generic_vao06_cli_validation_and_materialization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _standard_root("0.6.0")
    source = root / "Fixtures" / "VAO05" / "workspaces" / "minimal"
    workspace = tmp_path / "migrated"
    subprocess.run(
        [
            sys.executable,
            str(root / "Tools" / "migrate_vao05.py"),
            str(source),
            str(workspace),
            "--receipt",
            str(tmp_path / "migration.json"),
        ],
        check=True,
        capture_output=True,
    )
    fixture = tmp_path / "source.vao"
    subprocess.run(
        [
            sys.executable,
            str(root / "Tools" / "vao06.py"),
            "pack",
            str(workspace),
            str(fixture),
        ],
        check=True,
        capture_output=True,
    )
    assert main(["--json", "validate", str(fixture), "--standard-root", str(root)]) == 0
    validation = json.loads(capsys.readouterr().out)["referenceConformance"]
    assert validation["standardVersion"] == "0.6.0"
    assert validation["status"] == "core-valid"

    manifest_raw = (workspace / MANIFEST_NAME).read_bytes()
    manifest = json.loads(manifest_raw)
    realization = manifest["realizations"][0]
    output = tmp_path / "custom.vao"
    report = materialize_carrier(
        MemoryZenodoClient(fixture.read_bytes()),
        "10.5281/zenodo.1",
        output,
        realization_ids=[realization["id"]],
        standard_root=root,
    )
    assert report["verified"] is True
    assert report["referenceConformance"]["status"] == "core-valid"
    with zipfile.ZipFile(output) as archive:
        assert archive.read(MANIFEST_NAME) == manifest_raw
        carrier = json.loads(archive.read(CARRIER_NAME))
        assert (
            carrier["$schema"]
            == "https://w3id.org/modavis/vao/0.6.0/schema/carrier.json"
        )
        member = archive.read(carrier["embeddedRealizations"][0]["path"])
        assert hashlib.sha256(member).hexdigest() == realization["sha256"]


def test_native_mmic_distinguishes_complete_and_partial_inputs() -> None:
    root = _standard_root("0.6.0")
    fixture = root / "Fixtures" / "MMIC" / "1.0.0" / "roll"
    complete = run_reference_validator(
        fixture / "preservation-closure.vao",
        standard_root=root,
        required=True,
    )
    bootstrap = run_reference_validator(
        fixture / "bootstrap.vao",
        standard_root=root,
        required=True,
    )
    assert complete["status"] == "valid"
    assert complete["validation"]["coreValid"] is True
    assert complete["validation"]["mmic"]["levels"] == {
        "documentation": "valid",
        "decoding": "valid",
        "comparison": "valid",
    }
    assert bootstrap["valid"] is True
    assert bootstrap["status"] == "unmaterialized"
    assert bootstrap["validation"]["mmic"]["levels"]["documentation"] == "valid"
    assert bootstrap["validation"]["mmic"]["levels"]["decoding"] == "unmaterialized"


def _mmic_repository_fixture(
    root: Path, tmp_path: Path
) -> tuple[dict[str, bytes], str, bytes]:
    source = root / "Fixtures" / "MMIC" / "1.0.0" / "roll"
    manifest = json.loads((source / "bootstrap" / MANIFEST_NAME).read_bytes())
    preservation = json.loads(
        (source / "preservation-closure" / CARRIER_NAME).read_bytes()
    )
    member = next(
        item
        for item in preservation["embeddedRealizations"]
        if item["path"] == "payload/observation.svg"
    )
    identifier = member["realizationId"]
    selected = next(
        item for item in manifest["realizations"] if item["id"] == identifier
    )
    selected["distributionIds"] = ["urn:test:carrier-member"]
    repository_type = "https://w3id.org/modavis/vao/repository/zenodo"
    profile = "https://w3id.org/modavis/vao/profile/repository/zenodo/0.6.0"
    manifest["conformsTo"].append(profile)
    manifest["profiles"].append(
        {"id": profile, "version": "0.6.0", "requiredCapabilities": []}
    )
    manifest["repositoryBindings"] = [
        {
            "id": "urn:test:repository",
            "repositoryType": repository_type,
            "instance": "https://zenodo.org",
            "apiProfile": repository_type + "/records-api/1",
            "resolutionPolicy": "version-pid-record-file",
        }
    ]
    version_pid = "https://doi.org/10.5281/zenodo.1"
    manifest["distributions"] = [
        {
            "id": "urn:test:carrier-member",
            "kind": "carrier-member",
            "carrierId": preservation["id"],
            "repositoryBindingId": "urn:test:repository",
            "persistentIdentifier": version_pid,
            "recordIdentifier": "1",
            "fileIdentifier": "preservation-closure.vao",
            "access": "public",
        }
    ]
    manifest_raw = json_bytes(manifest)
    contents = {MANIFEST_NAME: manifest_raw}
    inventory = [
        {
            "fileIdentifier": MANIFEST_NAME,
            "role": "manifest",
            "byteSize": len(manifest_raw),
            "sha256": hashlib.sha256(manifest_raw).hexdigest(),
        }
    ]
    for mode in ("bootstrap", "preservation-closure"):
        name = f"{mode}.vao"
        carrier_dir = tmp_path / mode
        shutil.copytree(source / mode, carrier_dir)
        descriptor = json.loads((carrier_dir / CARRIER_NAME).read_bytes())
        descriptor["manifestByteSize"] = len(manifest_raw)
        descriptor["manifestSHA256"] = hashlib.sha256(manifest_raw).hexdigest()
        descriptor_raw = json_bytes(descriptor)
        (carrier_dir / MANIFEST_NAME).write_bytes(manifest_raw)
        (carrier_dir / CARRIER_NAME).write_bytes(descriptor_raw)
        carrier = tmp_path / name
        subprocess.run(
            [
                sys.executable,
                str(root / "Tools" / "vao06.py"),
                "pack",
                str(carrier_dir),
                str(carrier),
            ],
            check=True,
            capture_output=True,
        )
        carrier_raw = carrier.read_bytes()
        contents[name] = carrier_raw
        inventory.append(
            {
                "fileIdentifier": name,
                "role": "carrier",
                "byteSize": len(carrier_raw),
                "sha256": hashlib.sha256(carrier_raw).hexdigest(),
                "carrierId": descriptor["id"],
                "carrierMode": mode,
                "carrierDescriptorByteSize": len(descriptor_raw),
                "carrierDescriptorSHA256": hashlib.sha256(descriptor_raw).hexdigest(),
                **{
                    field: descriptor[field]
                    for field in (
                        "manifestSHA256",
                        "manifestByteSize",
                        "completeGroupIds",
                    )
                },
            }
        )
    release = {
        "$schema": "https://w3id.org/modavis/vao/0.6.0/schema/release.json",
        "type": "VAORelease",
        "formatVersion": "0.6.0",
        "vaoId": manifest["id"],
        "releaseId": manifest["release"]["id"],
        "revision": manifest["release"]["revision"],
        "contentVersion": manifest["release"]["contentVersion"],
        "publication": {
            "topology": "single-record",
            "rootRecord": {
                "id": "urn:test:record",
                "repositoryType": repository_type,
                "instance": "https://zenodo.org",
                "versionPersistentIdentifier": version_pid,
                "conceptPersistentIdentifier": "https://doi.org/10.5281/zenodo.10",
                "recordIdentifier": "1",
                "files": inventory,
            },
            "familyMembers": [],
        },
    }
    contents[RELEASE_NAME] = json_bytes(release)
    expected = (source / "preservation-closure" / member["path"]).read_bytes()
    return contents, identifier, expected


def test_native_mmic_selective_carrier_member_and_partial_materialization(
    tmp_path: Path,
) -> None:
    root = _standard_root("0.6.0")
    contents, realization_id, expected = _mmic_repository_fixture(root, tmp_path)
    client = MultiFileMemoryZenodoClient(contents)
    output = tmp_path / "observation.svg"
    result = fetch_realization(
        client,
        "10.5281/zenodo.1",
        realization_id,
        output,
        standard_root=root,
    )
    assert result["delivery"] == "carrier-member"
    assert result["verified"] is True
    assert result["manifestConformance"]["status"] == "unmaterialized"
    assert output.read_bytes() == expected

    output = tmp_path / "selection.vao"
    report = materialize_carrier(
        client,
        "10.5281/zenodo.1",
        output,
        realization_ids=[realization_id],
        standard_root=root,
    )
    assert report["verified"] is True
    assert report["referenceConformance"]["status"] == "unmaterialized"
    with zipfile.ZipFile(output) as archive:
        assert archive.read(MANIFEST_NAME) == contents[MANIFEST_NAME]
        realized = json.loads(archive.read(CARRIER_NAME))["embeddedRealizations"]
        assert len(realized) == 1
        assert archive.read(realized[0]["path"]) == expected

    release = json.loads(contents[RELEASE_NAME])
    release["publication"]["rootRecord"]["files"][0]["sha256"] = "0" * 64
    tampered = MultiFileMemoryZenodoClient(
        {**contents, RELEASE_NAME: json_bytes(release)}
    )
    rejected = tmp_path / "rejected.svg"
    with pytest.raises(IntegrityError, match="root manifest inventory"):
        fetch_realization(
            tampered, "10.5281/zenodo.1", realization_id, rejected, standard_root=root
        )
    assert not rejected.exists()
