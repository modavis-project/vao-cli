import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import write_vao
from vao_cli.cache import PersistentCache
from vao_cli.compare import compare_carriers
from vao_cli.local import extract_local_realization
from vao_cli.publication import prepare_publication
from vao_cli.selection import SelectionConstraints, parse_byte_size, select_realizations


class AdvancedTests(unittest.TestCase):
    def test_capability_selection_includes_group_dependencies(self):
        manifest = {
            "logicalAssets": [
                {
                    "id": "urn:test:asset",
                    "labels": {"en": "Dependency"},
                    "realizationIds": ["urn:test:realization"],
                }
            ],
            "realizations": [
                {
                    "id": "urn:test:realization",
                    "assetId": "urn:test:asset",
                    "byteSize": 1,
                    "qualityTier": "bootstrap",
                    "mediaType": "application/octet-stream",
                    "technicalMetadata": {"kind": "data"},
                }
            ],
            "assetGroups": [
                {
                    "id": "urn:test:group:root",
                    "realizationIds": [],
                    "dependsOnGroupIds": ["urn:test:group:dependency"],
                    "requiredCapabilities": ["urn:test:capability"],
                    "materializesProfileIds": [],
                },
                {
                    "id": "urn:test:group:dependency",
                    "realizationIds": ["urn:test:realization"],
                    "dependsOnGroupIds": [],
                    "requiredCapabilities": [],
                    "materializesProfileIds": [],
                },
            ],
        }
        matches = select_realizations(
            manifest, SelectionConstraints(capability="urn:test:capability")
        )
        self.assertEqual(
            [item["realizationId"] for item in matches], ["urn:test:realization"]
        )

    def test_selection_size_alias_and_local_extract(self):
        with tempfile.TemporaryDirectory() as folder:
            carrier = Path(folder) / "tiny.vao"
            output = Path(folder) / "audio.bin"
            manifest, payload = write_vao(carrier)
            matches = select_realizations(
                manifest,
                SelectionConstraints(
                    kind="audio", quality="preview", max_bytes=parse_byte_size("1MiB")
                ),
            )
            self.assertEqual(len(matches), 1)
            report = extract_local_realization(
                carrier, matches[0]["realizationId"], output
            )
            self.assertEqual(output.read_bytes(), payload)
            self.assertTrue(report["verified"])

    def test_cache_lifecycle_and_release_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = PersistentCache(root / "catalog.sqlite3")
            cache.put("json", "one", b"{}", ttl=60)
            self.assertEqual(cache.get("json", "one"), b"{}")
            self.assertEqual(cache.stats()["hits"], 1)
            left = root / "left.vao"
            right = root / "right.vao"
            write_vao(left)
            write_vao(right)
            comparison = compare_carriers(left, right)
            self.assertTrue(comparison["sameVAO"])
            self.assertEqual(comparison["summary"]["changed"], 0)

    def test_offline_publication_artifacts_remain_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            carrier = root / "tiny.vao"
            write_vao(carrier)
            valid = {"valid": True, "status": "conforming"}
            with (
                patch(
                    "vao_cli.publication.run_reference_validator",
                    return_value=valid,
                ),
                patch(
                    "vao_cli.publication.run_reference_descriptor_validator",
                    return_value=valid,
                ),
            ):
                report = prepare_publication(carrier, root / "staging")
            self.assertFalse(report["readyForLivePublication"])
            metadata = json.loads(
                (root / "staging" / "zenodo-metadata.template.json").read_text()
            )
            self.assertEqual(metadata["type"], "VAOZenodoMetadata")
            self.assertEqual(metadata["formatVersion"], "0.4.0")
            self.assertEqual(
                metadata["metadata"]["communities"][0]["identifier"],
                "virtual-acoustic-objects",
            )


if __name__ == "__main__":
    unittest.main()
