import json
import unittest
from unittest.mock import patch

from brave_websites import BraveWebsiteDiscovery


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return "https://api.search.brave.com/res/v1/web/search"
    def read(self, limit): return self.body[:limit]


class BraveDiscoveryTests(unittest.TestCase):
    def test_requires_key_only_when_discovery_is_enabled(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "BRAVE_SEARCH_API_KEY"):
                BraveWebsiteDiscovery()

    def test_searches_org_number_first_then_name_and_discards_result_text(self):
        responses = [
            FakeResponse({"web": {"results": []}}),
            FakeResponse({"web": {"results": [
                {"url": "https://company.example/about?utm_source=brave", "title": "secret title", "description": "secret snippet"},
                {"url": "https://www.linkedin.com/company/nope", "title": "Wrong"},
                {"url": "https://company.example/about", "title": "Duplicate"},
            ]}}),
        ]
        opener = unittest.mock.Mock()
        opener.open.side_effect = responses
        with patch("brave_websites.urllib.request.build_opener", return_value=opener):
            result = BraveWebsiteDiscovery(api_key="test").discover("923609016", "Example AS")
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertIn('q=%22923609016%22', first.full_url)
        self.assertIn('q=%22Example+AS%22', second.full_url)
        self.assertEqual(first.get_header("X-subscription-token"), "test")
        self.assertEqual(result["candidate_urls"], ["https://company.example/about"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["billable_requests"], 2)
        self.assertEqual(result["estimated_cost_usd"], 0.01)

    def test_no_results_and_request_budget_are_explicit(self):
        client = BraveWebsiteDiscovery(api_key="test", max_requests=1)
        opener = unittest.mock.Mock()
        opener.open.return_value = FakeResponse({"web": {"results": []}})
        with patch("brave_websites.urllib.request.build_opener", return_value=opener):
            result = client.discover("923609016", "Example AS")
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["requests"], 1)
        opener.open.assert_called_once()

    def test_rejects_social_and_unsafe_hosts(self):
        body = {"web": {"results": [
            {"url": "https://linkedin.com/company/example"},
            {"url": "https://127.0.0.1/admin"},
            {"url": "https://example.no/"},
        ]}}
        self.assertEqual(BraveWebsiteDiscovery._parse_candidates(json.dumps(body).encode(), 3),
                         ["https://example.no/"])


if __name__ == "__main__":
    unittest.main()
