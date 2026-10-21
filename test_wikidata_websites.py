import json
import unittest
from unittest.mock import patch

from wikidata_websites import WikidataWebsiteDiscovery, normalize_site_url


def profile(org, website=None):
    facts = [{"field": "website", "value": website}] if website else []
    return {
        "organisation_number": org,
        "identity_anchor": {"organisation_number": org, "status": "verified"},
        "claims": [{"family": "company_details", "facts": facts}],
    }


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return "https://query.wikidata.org/sparql"

    def read(self, _limit):
        return self.body


class WikidataWebsiteTests(unittest.TestCase):
    def test_normalizes_candidate_and_rejects_social_directory_or_unsafe_urls(self):
        self.assertEqual(normalize_site_url("example.no/about?x=1"), "https://example.no/about")
        self.assertEqual(normalize_site_url("http://example.no"), "https://example.no/")
        self.assertIsNone(normalize_site_url("https://linkedin.com/company/example"))
        self.assertIsNone(normalize_site_url("https://proff.no/example"))
        self.assertIsNone(normalize_site_url("https://user:pass@example.no"))
        self.assertIsNone(normalize_site_url("https://example.no:8443"))

    def test_query_requires_exact_org_number_and_valid_wikidata_item(self):
        body = json.dumps({"results": {"bindings": [
            {"org": {"value": "923609016"}, "item": {"value": "http://www.wikidata.org/entity/Q123"}, "site": {"value": "example.no"}},
            {"org": {"value": "987654321"}, "item": {"value": "http://www.wikidata.org/entity/Q456"}, "site": {"value": "wrong.example"}},
            {"org": {"value": "923609016"}, "item": {"value": "http://attacker.example/not-wikidata/Q9"}, "site": {"value": "evil.example"}},
            {"org": {"value": "923609016"}, "item": {"value": "http://www.wikidata.org/entity/Q999"}, "site": {"value": "https://facebook.com/company/example"}},
        ]}}).encode()
        mock_opener = unittest.mock.Mock()
        mock_opener.open.return_value = _Response(body)
        with patch("wikidata_websites.urllib.request.build_opener", return_value=mock_opener):
            client = WikidataWebsiteDiscovery()
            _raw, rows = client._query(["923609016"])
        self.assertEqual(rows, [{
            "organisation_number": "923609016",
            "item": "http://www.wikidata.org/entity/Q123",
            "qid": "Q123",
            "site": "https://example.no/",
        }])
        self.assertEqual(mock_opener.open.call_count, 1)
        self.assertEqual(client.requests, 0)  # _query itself does not own batch accounting

    def test_discovery_skips_unverified_and_only_skips_verified_website_profiles(self):
        client = WikidataWebsiteDiscovery(batch_size=1)
        verified = profile("111111111")
        verified["claims"].append({"family": "web_presence", "status": "available",
                                   "facts": [{"field": "website_url", "value": "https://verified.example"}]})
        rows = [profile("923609016"), profile("987654321", "https://known.example"), verified,
                {"organisation_number": "123456789", "identity_anchor": {"status": "failed"}, "claims": []}]
        with patch.object(client, "_query", return_value=(b"{}", [])) as query:
            candidates, report = client.discover(rows)
        self.assertEqual([call.args[0] for call in query.call_args_list], [["923609016"], ["987654321"]])
        self.assertFalse(candidates)
        self.assertEqual(report["target_count"], 2)
        self.assertEqual(report["status"], "complete")

    def test_query_failure_is_explicit_and_does_not_raise_out_of_batch(self):
        client = WikidataWebsiteDiscovery(batch_size=1)
        with patch.object(client, "_query", side_effect=TimeoutError("timeout")):
            candidates, report = client.discover([profile("923609016"), profile("987654321")])
        self.assertFalse(candidates)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["requests"], 2)
        self.assertEqual(len(report["errors"]), 2)


if __name__ == "__main__":
    unittest.main()
