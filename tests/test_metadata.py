import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import write_vao
from vao_cli.local import validate_local_carrier
from vao_cli.metadata import apply_metadata, metadata_projection


class MetadataTests(unittest.TestCase):
    def test_projection_and_revisioned_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.vao"
            output = Path(folder) / "edited.vao"
            document = Path(folder) / "metadata.json"
            _manifest, payload = write_vao(source)
            projection = metadata_projection(source)
            projection["title"] = {"en": "Edited Tiny VAO"}
            projection["contentVersion"] = "1.1.0"
            document.write_text(json.dumps(projection), encoding="utf-8")
            with patch(
                "vao_cli.metadata.run_reference_validator",
                return_value={"valid": True, "status": "conforming"},
            ):
                report = apply_metadata(source, document, output)
            self.assertEqual(report["revision"], 2)
            result = validate_local_carrier(output, verify_payloads=True)
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(result["manifest"]["title"]["en"], "Edited Tiny VAO")
            self.assertEqual(result["verifiedPayloadBytes"], len(payload))


if __name__ == "__main__":
    unittest.main()
