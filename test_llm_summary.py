import json
import unittest
from unittest.mock import patch

from llm_summary import LLMSummaryAgent, SYSTEM_PROMPT, _catalog, _unknown_line


def profile():
    return {
        "organisation_number": "923609016",
        "identity_anchor": {"status": "verified", "legal_name": "Example AS"},
        "source_snapshots": [
            {"id": "registry:one", "family": "registry"},
            {"id": "financials:one", "family": "financials"},
        ],
        "claims": [
            {"family": "company_details", "status": "available", "facts": [
                {"field": "legal_name", "value": "Example AS", "evidence_ids": ["registry:one"]},
                {"field": "industry_description", "value": "Software development", "evidence_ids": ["registry:one"], "observed_at": "2026-10-19T00:00:00Z"},
                {"field": "city", "value": "Oslo", "evidence_ids": ["registry:one"]},
            ], "reason": None},
            {"family": "financials", "status": "available", "facts": [
                {"field": "revenue_nok", "value": 750000, "evidence_ids": ["financials:one"], "valid_at": "2025-12-31", "reporting_period": {"from": "2025-01-01", "to": "2025-12-31"}},
            ], "reason": None},
            {"family": "jobs", "status": "not_available", "facts": [], "reason": "No verified vacancy"},
        ],
        "changes": [],
    }


class FakeResponse:
    def __init__(self, payload): self.body = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit): return self.body[:limit]


class LLMSummaryTests(unittest.TestCase):
    def test_system_prompt_is_the_user_supplied_plain_text_prompt(self):
        for phrase in (
            "Use ONLY the facts provided in the input",
            "If a category", "state the reporting period/year", "Write 3-5 sentences",
            "Output: plain text only, no JSON, no markdown formatting.",
        ):
            self.assertIn(phrase, SYSTEM_PROMPT)
        self.assertNotIn("Return only JSON", SYSTEM_PROMPT)

    def test_key_is_required_and_unverified_identity_makes_no_call(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                LLMSummaryAgent()
        result = LLMSummaryAgent(api_key="test").summarize({"identity_anchor": {"status": "ambiguous"}})
        self.assertEqual(result["calls_made"], 0)
        self.assertEqual(result["status"], "skipped_unverified_identity")

    def test_prompt_contains_only_collected_claim_fields_and_no_source_content(self):
        row = profile()
        catalog, _ = _catalog(row)
        self.assertEqual(len(catalog), 4)
        with patch("llm_summary.urllib.request.urlopen") as urlopen:
            urlopen.return_value = FakeResponse({"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Example AS is registered in Oslo. It is listed for software development. Revenue was NOK 750,000 in 2025. The following could not be confirmed: current jobs."}
            ]}], "usage": {"input_tokens": 20, "output_tokens": 25}})
            result = LLMSummaryAgent(api_key="test").summarize(row)
            request = json.loads(urlopen.call_args.args[0].data)
        prompt = request["input"][1]["content"][0]["text"]
        payload = json.loads(prompt.split("\n", 1)[1].split("\n\nWrite the summary", 1)[0])
        self.assertEqual(payload["identity_anchor"], row["identity_anchor"])
        self.assertEqual(payload["claims"], [{key: claim.get(key) for key in ("family", "status", "facts", "reason")} for claim in row["claims"]])
        self.assertEqual(payload["changes"], row["changes"])
        self.assertNotIn("source_snapshots", prompt)
        self.assertNotIn("source excerpt", prompt.lower())
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["summary"]["text"].count("."), 4)

    def test_plain_prose_validation_keeps_evidence_catalog_and_missing_sentence_last(self):
        row = profile()
        catalog, refs = _catalog(row)
        missing = _unknown_line(row)
        text = "Example AS is registered in Oslo. It is listed for software development. Revenue was NOK 750,000 for 2025. " + missing
        summary, error = LLMSummaryAgent._validate(text, catalog, refs, missing)
        self.assertIsNone(error)
        self.assertEqual(len(summary["claim_map"]), 3)
        self.assertEqual(set(summary["evidence_ids"]), {"registry:one", "financials:one"})
        self.assertTrue(summary["text"].endswith(missing))

    def test_invalid_format_missing_clause_and_unsupported_numbers_fail_closed(self):
        row = profile()
        catalog, refs = _catalog(row)
        missing = _unknown_line(row)
        invalids = [
            ("{\"summary\": \"bad\"}", "summary_not_plain_prose"),
            ("Example AS is registered in Oslo. Revenue was NOK 750,000 in 2025. There are 12 staff. " + missing, "unsupported_numeric_value"),
            ("Example AS is in Oslo. Revenue was NOK 750,000 in 2025. Software development is listed.", "missing_categories_sentence_must_be_last"),
            ("- Example AS is in Oslo.\n- Revenue is NOK 750,000 in 2025.\n- Software development is listed.\n" + missing, "summary_not_plain_prose"),
        ]
        for text, expected in invalids:
            with self.subTest(expected=expected):
                summary, error = LLMSummaryAgent._validate(text, catalog, refs, missing)
                self.assertIsNone(summary)
                self.assertEqual(error, expected)

    def test_all_found_categories_do_not_add_unconfirmed_sentence(self):
        row = profile()
        row["claims"] = [claim for claim in row["claims"] if claim["family"] != "jobs"]
        self.assertEqual(_unknown_line(row), "")
        catalog, refs = _catalog(row)
        text = "Example AS is registered in Oslo. It is listed for software development. Revenue was NOK 750,000 in 2025."
        summary, error = LLMSummaryAgent._validate(text, catalog, refs, "")
        self.assertIsNone(error)
        self.assertEqual(len(summary["text"].split(". ")), 3)


if __name__ == "__main__":
    unittest.main()
