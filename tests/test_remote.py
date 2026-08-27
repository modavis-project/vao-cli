import tempfile
import unittest
from contextlib import contextmanager
from email.message import Message
from io import BytesIO
from pathlib import Path

from tests.support import MemoryHTTP, MemoryZenodoClient, make_pack_vao, make_vao
from vao_cli.fetch import fetch_realization
from vao_cli.group_fetch import fetch_group
from vao_cli.models import PRODUCTION, RemoteFile, ResolvedRecord
from vao_cli.remote_zip import RemoteZipReader
from vao_cli.resolver import VAOResolver


class RemoteTests(unittest.TestCase):
    def test_range_reads_manifest_from_remote_zip(self):
        data, manifest, _payload = make_vao()
        http = MemoryHTTP(data)
        reader = RemoteZipReader(http, "https://zenodo.org/test", len(data))
        raw = reader.read(
            reader.require_entry("vao-manifest.json"), maximum=1024 * 1024
        )
        self.assertIn(manifest["id"].encode(), raw)
        self.assertTrue(http.ranges)
        self.assertTrue(
            all(start >= 0 and end < len(data) for start, end in http.ranges)
        )

    def test_resolver_inspects_without_whole_download(self):
        data, _manifest, _payload = make_vao()
        client = MemoryZenodoClient(data)
        result = VAOResolver(client).inspect("10.5281/zenodo.1")
        self.assertEqual(result.manifest["formatVersion"], "0.4.0")
        self.assertEqual(result.carrier["carrierMode"], "bootstrap")

    def test_fetches_one_realization_by_range(self):
        data, _manifest, payload = make_vao()
        client = MemoryZenodoClient(data)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "audio.bin"
            report = fetch_realization(
                client,
                "10.5281/zenodo.1",
                "urn:test:realization:audio",
                output,
                conformance=False,
            )
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(report["byteSize"], len(payload))
            self.assertLess(report["compressedSize"], len(data))

    def test_fetches_verified_chunk_extent(self):
        data, manifest, payload = make_vao()
        client = MemoryZenodoClient(data)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "chunk.bin"
            report = fetch_realization(
                client,
                "10.5281/zenodo.1",
                manifest["realizations"][0]["id"],
                output,
                chunks="0",
                conformance=False,
            )
            expected = payload[: len(payload) // 2]
            self.assertEqual(output.read_bytes(), expected)
            self.assertTrue(report["partial"])
            self.assertEqual(report["chunks"], [0])

    def test_fetches_member_from_embedded_stored_pack(self):
        data, identifier, payload = make_pack_vao()
        client = MemoryZenodoClient(data)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "model.glb"
            report = fetch_realization(
                client,
                "10.5281/zenodo.1",
                identifier,
                output,
                conformance=False,
            )
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(report["delivery"], "pack-member")
            self.assertEqual(report["verification"]["outerPack"], "not-fully-read")

    def test_fetches_exact_repository_distribution(self):
        root, payload, identifier = _repository_carrier()
        client = RepositoryClient(root, payload)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "external.bin"
            report = fetch_realization(
                client,
                "10.5281/zenodo.1",
                identifier,
                output,
                conformance=False,
            )
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(report["delivery"], "repository")
            self.assertEqual(report["distributionDOI"], "10.5281/zenodo.2")

    def test_transactionally_fetches_asset_group(self):
        data, _manifest, payload = make_vao()
        client = MemoryZenodoClient(data)
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "bootstrap"
            report = fetch_group(
                client,
                "10.5281/zenodo.1",
                "urn:test:group:bootstrap",
                destination,
                conformance=False,
            )
            self.assertTrue(report["verified"])
            outputs = list(destination.iterdir())
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0].read_bytes(), payload)


def _repository_carrier():
    import hashlib
    import json
    import zipfile

    payload = b"external exact realization\n"
    identifier = "urn:test:realization:external"
    manifest = {
        "$schema": "https://w3id.org/modavis/vao/0.4.0/schema/manifest.json",
        "@context": ["https://w3id.org/modavis/vao/0.4.0/context.jsonld"],
        "type": "VirtualAcousticObject",
        "formatVersion": "0.4.0",
        "id": "urn:test:vao:repository",
        "title": {"en": "Repository VAO"},
        "release": {
            "id": "urn:test:release:repository:1",
            "revision": 1,
            "contentVersion": "1",
        },
        "logicalAssets": [
            {
                "id": "urn:test:asset:external",
                "labels": {"en": "External"},
                "realizationIds": [identifier],
            }
        ],
        "realizations": [
            {
                "id": identifier,
                "assetId": "urn:test:asset:external",
                "byteSize": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "mediaType": "application/octet-stream",
                "qualityTier": "preservation",
                "technicalMetadata": {"kind": "data"},
                "distributionIds": ["urn:test:distribution:external"],
            }
        ],
        "distributions": [
            {
                "id": "urn:test:distribution:external",
                "kind": "repository",
                "repositoryBindingId": "urn:test:binding:zenodo",
                "persistentIdentifier": "https://doi.org/10.5281/zenodo.2",
                "recordIdentifier": "2",
                "fileIdentifier": "external.bin",
                "access": "public",
            }
        ],
        "repositoryBindings": [
            {
                "id": "urn:test:binding:zenodo",
                "repositoryType": "https://w3id.org/modavis/vao/repository/zenodo",
                "instance": "https://zenodo.org",
                "apiProfile": "https://w3id.org/modavis/vao/repository/zenodo/records-api/1",
                "resolutionPolicy": "version-pid-record-file",
            }
        ],
        "assetGroups": [],
    }
    manifest_raw = json.dumps(manifest, sort_keys=True).encode() + b"\n"
    carrier = {
        "formatVersion": "0.4.0",
        "type": "VAOCarrier",
        "carrierMode": "custom",
        "releaseId": manifest["release"]["id"],
        "manifestByteSize": len(manifest_raw),
        "manifestSHA256": hashlib.sha256(manifest_raw).hexdigest(),
        "completeGroupIds": [],
        "embeddedRealizations": [],
    }
    root = BytesIO()
    with zipfile.ZipFile(root, "w") as archive:
        archive.writestr("mimetype", b"application/vnd.modavis.vao+zip")
        archive.writestr("vao-manifest.json", manifest_raw)
        archive.writestr("META-INF/vao-carrier.json", json.dumps(carrier).encode())
    return root.getvalue(), payload, identifier


class RepositoryHTTP:
    def __init__(self, values):
        self.values = values

    def get_range(self, url, start, end):
        return self.values[url][start : end + 1], Message()

    @contextmanager
    def open(self, url, *, headers=None):
        response = BytesIO(self.values[url])
        response.status = 200
        response.getcode = lambda: response.status
        yield response


class RepositoryClient:
    root_url = "https://zenodo.org/api/records/1/files/root/content"
    external_url = "https://zenodo.org/api/records/2/files/external/content"

    def __init__(self, root, payload):
        self.instance = PRODUCTION
        self.http = RepositoryHTTP({self.root_url: root, self.external_url: payload})
        self.records = {
            "1": {"id": 1, "doi": "10.5281/zenodo.1", "metadata": {"title": "Root"}},
            "2": {"id": 2, "doi": "10.5281/zenodo.2", "metadata": {"title": "Data"}},
        }

    def resolve(self, doi, *, allow_concept=True):
        record_id = "2" if str(doi).endswith(".2") else "1"
        return ResolvedRecord(
            str(doi),
            f"10.5281/zenodo.{record_id}",
            None,
            record_id,
            PRODUCTION,
            self.records[record_id],
        )

    def for_resolved(self, _resolved):
        return self

    def files(self, record):
        if str(record["id"]) == "2":
            data = self.http.values[self.external_url]
            return [RemoteFile("external.bin", len(data), None, self.external_url)]
        data = self.http.values[self.root_url]
        return [RemoteFile("root.vao", len(data), None, self.root_url)]


if __name__ == "__main__":
    unittest.main()
