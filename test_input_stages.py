import json
import tempfile
import unittest
from pathlib import Path

import signalpost_agent as sp


class InputPipelineStageTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.cwd() / "work"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)

    def write(self, suffix, content):
        path = Path(self.temp.name) / ("batch" + suffix)
        path.write_text(content, encoding="utf-8")
        return path

    def test_normalization_accepts_only_documented_display_separators(self):
        self.assertEqual(sp.normalize_org(" 923-609-016\t"), "923609016")
        self.assertEqual(sp.normalize_org("923.609.016"), "923.609.016")
        self.assertEqual(sp.normalize_org("+47 923 609 016"), "+47923609016")

    def test_checksum_separates_valid_and_single_digit_corruption(self):
        self.assertTrue(sp.valid_org(sp.normalize_org("923 609 016")))
        self.assertFalse(sp.valid_org(sp.normalize_org("923 609 017")))
        self.assertFalse(sp.valid_org("12345678"))
        self.assertFalse(sp.valid_org("923.609.016"))

    def test_json_array_batch_extracts_values_and_preserves_duplicate_inputs(self):
        path = self.write(".json", json.dumps(["923 609 016", {"orgnr": "923-609-016"}]))
        self.assertEqual(sp.load_orgs(path), ["923609016", "923609016"])

    def test_jsonl_and_plain_text_batches_each_parse_one_number_per_record(self):
        jsonl = self.write(".jsonl", '{"organisation_number":"923 609 016"}\n"923-609-016"\n')
        plain = self.write(".txt", "923 609 016\n\n923-609-016\n")
        self.assertEqual(sp.load_orgs(jsonl), ["923609016", "923609016"])
        self.assertEqual(sp.load_orgs(plain), ["923609016", "923609016"])

    def test_unicode_numerals_are_rejected_as_not_ascii_org_numbers(self):
        arabic_indic = "٩٢٣٦٠٩٠١٦"
        self.assertFalse(sp.valid_org(arabic_indic))


if __name__ == "__main__":
    unittest.main()
