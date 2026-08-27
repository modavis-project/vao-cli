import unittest

from vao_cli.doi import normalize_doi, zenodo_record_hint
from vao_cli.errors import ResolutionError
from vao_cli.models import PRODUCTION, SANDBOX, ResolvedRecord
from vao_cli.zenodo import ZenodoClient


class DOITests(unittest.TestCase):
    def test_normalizes_doi_urls(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.5281/Zenodo.123"), "10.5281/zenodo.123"
        )
        instance, identifier = zenodo_record_hint("10.5281/zenodo.123")
        self.assertEqual(instance.name, "production")
        self.assertEqual(identifier, "123")

    def test_rejects_non_doi(self):
        with self.assertRaises(ResolutionError):
            normalize_doi("zenodo.123")

    def test_client_rebinds_to_resolved_instance(self):
        resolved = ResolvedRecord(
            "10.5072/zenodo.1",
            "10.5072/zenodo.1",
            None,
            "1",
            SANDBOX,
            {"id": 1},
        )
        bound = ZenodoClient(PRODUCTION).for_resolved(resolved)
        self.assertEqual(bound.instance, SANDBOX)
        self.assertEqual(bound.http.instance, SANDBOX)


if __name__ == "__main__":
    unittest.main()
