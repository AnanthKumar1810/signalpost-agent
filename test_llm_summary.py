import json
import urllib.error
from email.message import Message
import unittest
from unittest.mock import patch

from llm_summary import LLMSummaryAgent, SYSTEM_PROMPT, _catalog, _missing_family_groups, _unknown_line


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

    def test_http_429_opens_circuit_and_preserves_retry_after(self):
        headers = Message()
        headers["Retry-After"] = "45"
        rate_limit = urllib.error.HTTPError("https://api.openai.com/v1/responses", 429, "rate limited", headers, None)
        agent = LLMSummaryAgent(api_key="test")
        with patch("llm_summary.urllib.request.urlopen", side_effect=rate_limit) as urlopen:
            first = agent.summarize(profile())
            second = agent.summarize(profile())
        self.assertEqual(first["http_status"], 429)
        self.assertEqual(first["retry_after"], "45")
        self.assertEqual(second["status"], "circuit_open")
        self.assertEqual(second["calls_made"], 0)
        self.assertEqual(urlopen.call_count, 1)

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
        self.assertEqual(summary["claim_map"][0]["field_refs"], ["company_details.city#3", "company_details.legal_name#1"])
        self.assertEqual(summary["claim_map"][2]["field_refs"], ["financials.revenue_nok#1"])
        self.assertEqual(set(summary["evidence_ids"]), {"registry:one", "financials:one"})
        self.assertTrue(summary["text"].endswith(missing))

    def test_invalid_format_missing_clause_and_unsupported_numbers_fail_closed(self):
        row = profile()
        catalog, refs = _catalog(row)
        missing = _unknown_line(row)
        invalids = [
            ("{\"summary\": \"bad\"}", "summary_not_plain_prose"),
            ("Example AS is registered in Oslo. Revenue was NOK 750,000 in 2025. There are 12 staff. " + missing, "unsupported_numeric_value"),
            ("Example AS is registered in Oslo. Revenue was NOK 750,000 in 2025. It is a fast-growing market leader. " + missing, "unsupported_factual_sentence"),
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

    def test_verified_identity_number_is_allowed_as_an_input_supported_number(self):
        row = profile()
        catalog, refs = _catalog(row)
        missing = _unknown_line(row)
        text = "Example AS, organisation number 923609016, is registered in Oslo. Revenue was NOK 750,000 for 2025. " + missing
        summary, error = LLMSummaryAgent._validate(
            text, catalog, refs, missing, additional_numeric_values={
                "identity_anchor": row["identity_anchor"], "organisation_number": row["organisation_number"]})
        self.assertIsNone(error)
        self.assertIsNotNone(summary)

    def test_cost_estimate_is_available_even_when_summary_is_rejected(self):
        estimate = LLMSummaryAgent._estimate_cost({"input_tokens": 1000, "output_tokens": 100}, "gpt-6-luna")
        self.assertEqual(estimate, 0.000075)

    def test_sentence_split_handles_a_period_inside_a_quoted_description(self):
        row = profile()
        row["claims"].append({"family": "web_presence", "status": "available", "facts": [
            {"field": "website_description", "value": "Software.", "evidence_ids": ["registry:one"]},
        ]})
        catalog, refs = _catalog(row)
        missing = _unknown_line(row)
        text = 'Example AS is registered. Its website describes "Software." Revenue was NOK 750,000 for 2025. ' + missing
        summary, error = LLMSummaryAgent._validate(text, catalog, refs, missing, _missing_family_groups(row))
        self.assertIsNone(error)
        self.assertIsNotNone(summary)


if __name__ == "__main__":
    unittest.main()
