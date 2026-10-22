import json
import unittest
from unittest.mock import patch

from tinyfish_websites import TinyFishWebsiteDiscovery


class FakeResponse:
    def __init__(self, payload): self.body = json.dumps(payload).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def geturl(self): return "https://api.search.tinyfish.ai"
    def read(self, limit): return self.body[:limit]


class TinyFishDiscoveryTests(unittest.TestCase):
    def test_requires_key_only_when_enabled(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TINYFISH_API_KEY"):
                TinyFishWebsiteDiscovery()

    def test_org_then_name_and_search_text_is_discarded(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = [FakeResponse({"results": []}), FakeResponse({"results": [
            {"url": "https://example.no/about?tracking=1", "title": "secret", "snippet": "secret text"},
            {"url": "https://proff.no/example", "title": "directory"},
        ]})]
        with patch("tinyfish_websites.urllib.request.build_opener", return_value=opener):
            result = TinyFishWebsiteDiscovery(api_key="test").discover("923609016", "Example AS")
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertIn("query=%22923609016%22", first.full_url)
        self.assertIn("query=%22Example+AS%22", second.full_url)
        self.assertEqual(first.get_header("X-api-key"), "test")
        self.assertEqual(result["candidate_urls"], ["https://example.no/about"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["requests"], 2)

    def test_directory_private_and_social_results_are_rejected(self):
        payload = {"results": [
            {"url": "https://virksomhet.brreg.no/nb/oppslag/enheter/923609016"},
            {"url": "https://forvalt.no/Nettbutikk/produkter/923609016"},
            {"url": "https://www.1850.no/bedrifter/example"},
            {"url": "https://firmalisten.no/923609016"},
            {"url": "https://kvantil.no/company/923609016/concentration"},
            {"url": "https://aksjegrafen.com/selskap/923609016"},
            {"url": "https://rosa.no/tlf/example/923609016"},
            {"url": "https://kaspi.kz/shop/p/923609016"},
            {"url": "https://linkedin.com/company/example"},
            {"url": "https://127.0.0.1/admin"},
            {"url": "https://example.no/"},
        ]}
        self.assertEqual(TinyFishWebsiteDiscovery._parse_candidates(json.dumps(payload).encode(), 3),
                         ["https://example.no/"])

    def test_request_budget_and_failure_are_explicit(self):
        client = TinyFishWebsiteDiscovery(api_key="test", max_requests=1)
        opener = unittest.mock.Mock()
        opener.open.return_value = FakeResponse({"results": []})
        with patch("tinyfish_websites.urllib.request.build_opener", return_value=opener):
            result = client.discover("923609016", "Example AS")
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["requests"], 1)

    def test_domain_mismatch_is_retained_as_a_candidate_for_identity_verification(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = [
            FakeResponse({"results": [{"url": "https://generic-news.example/company/923609016"}]}),
            FakeResponse({"results": [{"url": "https://www.example.no/"}]}),
        ]
        with patch("tinyfish_websites.urllib.request.build_opener", return_value=opener):
            result = TinyFishWebsiteDiscovery(api_key="test").discover("923609016", "Example AS")
        self.assertEqual(result["candidate_urls"], ["https://generic-news.example/company/923609016"])
        self.assertEqual(result["requests"], 1)


if __name__ == "__main__":
    unittest.main()
