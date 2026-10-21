import json
import unittest
from unittest.mock import patch

from tavily_websites import TavilyWebsiteDiscovery
from tinyfish_websites import _domain_matches_name


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return "https://api.tavily.com/search"
    def read(self, limit): return self.body[:limit]


class TavilyDiscoveryTests(unittest.TestCase):
    def test_requires_key_only_when_enabled(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TAVILY_API_KEY"):
                TavilyWebsiteDiscovery()

    def test_searches_org_then_name_and_discards_search_text(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = [
            FakeResponse({"query": '"923609016"', "results": [{"url": "https://proff.no/company/923609016"}], "usage": {"credits": 1}}),
            FakeResponse({"query": '"Nordic Coffee AS"', "results": [
                {"url": "https://nordiccoffee.no/about?utm_source=tavily", "title": "private title", "content": "private snippet"},
                {"url": "https://linkedin.com/company/nordiccoffee", "title": "private title"},
            ], "usage": {"credits": 1}}),
        ]
        with patch("tavily_websites.urllib.request.build_opener", return_value=opener):
            result = TavilyWebsiteDiscovery(api_key="test").discover("923609016", "Nordic Coffee AS")
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(first.full_url, "https://api.tavily.com/search")
        self.assertIn("923609016", first.data.decode())
        self.assertIn("Nordic Coffee AS", second.data.decode())
        self.assertEqual(first.get_header("Authorization"), "Bearer test")
        self.assertEqual(result["candidate_urls"], ["https://nordiccoffee.no/about"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["credits_used"], 2)

    def test_rejects_directory_and_private_ip_but_keeps_domain_mismatch_as_candidate(self):
        payload = {"results": [
            {"url": "https://proff.no/company/nordic"},
            {"url": "https://127.0.0.1/admin"},
            {"url": "https://academic.example/"},
            {"url": "https://nordiccoffee.no/"},
        ]}
        urls, credits = TavilyWebsiteDiscovery._parse_candidates(json.dumps(payload).encode(), 5)
        self.assertEqual(urls, ["https://academic.example/", "https://nordiccoffee.no/"])
        self.assertEqual(credits, 0)

    def test_request_budget_and_network_failure_are_explicit(self):
        client = TavilyWebsiteDiscovery(api_key="test", max_requests=1)
        opener = unittest.mock.Mock()
        opener.open.side_effect = OSError("offline")
        with patch("tavily_websites.urllib.request.build_opener", return_value=opener):
            result = client.discover("923609016", "Nordic Coffee AS")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["requests"], 1)
        self.assertEqual(client.errors, ["OSError"])
        self.assertEqual(opener.open.call_count, 1)

    def test_rejects_cross_host_redirect(self):
        from tavily_websites import _FixedHostRedirect
        import urllib.request
        handler = _FixedHostRedirect()
        request = urllib.request.Request("https://api.tavily.com/search")
        with self.assertRaisesRegex(ValueError, "escaped its fixed HTTPS host"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/steal")


if __name__ == "__main__":
    unittest.main()
