import tempfile
import unittest
from pathlib import Path

from vao_cli.catalog import Catalog


class CommunityClient:
    def __init__(self, records):
        self.records = records

    def community_records(self, _slug):
        return self.records


def record(identifier: int, version: str, date: str):
    return {
        "id": identifier,
        "doi": f"10.5281/zenodo.{identifier}",
        "conceptdoi": "10.5281/zenodo.100",
        "created": f"{date}T00:00:00Z",
        "metadata": {
            "title": "Catalog VAO",
            "publication_date": date,
            "version": version,
        },
    }


class CatalogTests(unittest.TestCase):
    def test_tracks_new_and_new_version(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.sqlite3"
            with Catalog(path) as catalog:
                first = catalog.sync(
                    CommunityClient([record(101, "1.0", "2026-01-01")])
                )
                self.assertEqual(first["newVersionCount"], 1)
                self.assertEqual(catalog.list()[0]["status"], "new")
                catalog.acknowledge()
                catalog.sync(CommunityClient([record(102, "2.0", "2026-02-01")]))
                latest = catalog.list()[0]
                self.assertEqual(latest["doi"], "10.5281/zenodo.102")
                self.assertEqual(latest["status"], "updated")
                versions = catalog.list(all_versions=True)
                self.assertEqual(versions[0]["status"], "updated")


if __name__ == "__main__":
    unittest.main()
