import datetime as dt
import json
import unittest
from unittest.mock import patch

from nav_jobs import NavJobFeed, _NavRedirect, _name_is_candidate, _name_key


def profile(org="923609016", name="Example AS", municipality="Oslo", website="https://example.no"):
    return {
        "organisation_number": org,
        "identity_anchor": {"organisation_number": org, "legal_name": name, "status": "verified"},
        "claims": [{"family": "company_details", "facts": [
            {"field": "municipality", "value": municipality}, {"field": "website", "value": website}]},
            {"family": "jobs", "status": "not_available", "facts": [], "reason": "No website result"}],
    }


class NavJobFeedTests(unittest.TestCase):
    def fake_run(self, detail, business_name="Example AS"):
        page = {"items": [{"id": "ad-1", "url": "/api/v1/feedentry/ad-1", "_feed_entry": {
            "uuid": "ad-1", "status": "ACTIVE", "businessName": business_name, "title": "Engineer"}}], "next_url": None}
        responses = [(b"token\nheader\neyJhbGci.eyJzdWIi.signature", None), (json.dumps(page).encode(), page), (json.dumps(detail).encode(), detail)]
        def get(_self, url, token=None, modified_since=None):
            _self.requests += 1
            return responses.pop(0)
        return get

    def test_normalizes_norwegian_name_and_company_suffix(self):
        self.assertEqual(_name_key("Ålesund Company ASA"), "alesund company")

    def test_similar_feed_name_is_only_a_candidate_and_exact_org_detail_decides(self):
        self.assertTrue(_name_is_candidate("Example Group", "example"))
        self.assertFalse(_name_is_candidate("Unrelated Group", "example"))
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).isoformat()
        detail = {"status": "ACTIVE", "ad_content": {"title": "Engineer", "expires": expiry,
            "employer": {"name": "Example Group", "orgnr": "923609016"},
            "workLocations": [{"municipal": "Bergen"}]}}
        row = profile()
        with patch.object(NavJobFeed, "_get", self.fake_run(detail, "Example Group")):
            report = NavJobFeed().enrich([row])
        self.assertEqual(report["matched_profiles"], 1)

    def test_accepts_active_exact_org_job_with_expiry(self):
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).isoformat()
        detail = {"status": "ACTIVE", "ad_content": {"title": "Engineer", "published": "2026-10-15T00:00:00Z",
            "expires": expiry, "employer": {"name": "Example", "orgnr": "923609016"},
            "workLocations": [{"municipal": "Oslo"}], "applicationUrl": "https://jobs.example/app"}}
        row = profile()
        client = NavJobFeed()
        with patch.object(NavJobFeed, "_get", self.fake_run(detail)):
            report = client.enrich([row])
        self.assertEqual(report["matched_profiles"], 1)
        job = next(c for c in row["claims"] if c["family"] == "jobs")
        self.assertEqual(job["status"], "available")
        self.assertEqual(job["facts"][0]["value"]["title"], "Engineer")
        self.assertEqual(job["facts"][0]["_nav_url"], "https://pam-stilling-feed.nav.no/api/v1/feedentry/ad-1")
        self.assertEqual(report["lookback_days"], 180)
        self.assertEqual(report["candidate_details_queued"], 1)
        self.assertEqual(report["details_fetched"], 1)
        self.assertEqual(report["entity_mismatches"], 0)

    def test_rejects_stale_ad_even_when_feed_header_says_active(self):
        detail = {"status": "INACTIVE", "ad_content": {"title": "Engineer"}}
        row = profile()
        with patch.object(NavJobFeed, "_get", self.fake_run(detail)):
            report = NavJobFeed().enrich([row])
        self.assertEqual(report["matched_profiles"], 0)
        self.assertEqual(next(c for c in row["claims"] if c["family"] == "jobs")["status"], "not_available")

    def test_rejects_wrong_employer_org_number(self):
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).isoformat()
        detail = {"status": "ACTIVE", "ad_content": {"title": "Engineer", "expires": expiry,
            "employer": {"name": "Example", "orgnr": "987654321"}, "workLocations": [{"municipal": "Oslo"}]}}
        row = profile()
        with patch.object(NavJobFeed, "_get", self.fake_run(detail)):
            report = NavJobFeed().enrich([row])
        self.assertEqual(report["matched_profiles"], 0)

    def test_exact_name_candidates_get_detail_budget_before_similar_names(self):
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).isoformat()
        wrong = {"status": "ACTIVE", "ad_content": {"title": "Wrong", "expires": expiry,
            "employer": {"name": "Example Group", "orgnr": "999999999"}}}
        exact = {"status": "ACTIVE", "ad_content": {"title": "Right", "expires": expiry,
            "employer": {"name": "Example AS", "orgnr": "923609016"}}}
        page = {"items": [
            {"id": "similar", "url": "/api/v1/feedentry/similar", "_feed_entry": {"uuid": "similar", "status": "ACTIVE", "businessName": "Example Group"}},
            {"id": "exact", "url": "/api/v1/feedentry/exact", "_feed_entry": {"uuid": "exact", "status": "ACTIVE", "businessName": "Example AS"}},
        ], "next_url": None}
        responses = [(b"token\nheader\neyJhbGci.eyJzdWIi.signature", None), (json.dumps(page).encode(), page),
                     (json.dumps(exact).encode(), exact)]
        requested = []
        def get(_self, url, token=None, modified_since=None):
            requested.append(url)
            _self.requests += 1
            return responses.pop(0)
        row = profile()
        with patch.object(NavJobFeed, "_get", get):
            report = NavJobFeed(max_details=1).enrich([row])
        self.assertTrue(requested[-1].endswith("/exact"))
        self.assertEqual(report["matched_profiles"], 1)
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["truncated"])

    def test_missing_employer_org_number_requires_name_and_location_or_homepage(self):
        expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=4)).isoformat()
        detail = {"status": "ACTIVE", "ad_content": {"title": "Engineer", "expires": expiry,
            "employer": {"name": "Different name", "orgnr": None, "homepage": "https://other.example"},
            "workLocations": [{"municipal": "Bergen"}]}}
        row = profile()
        with patch.object(NavJobFeed, "_get", self.fake_run(detail)):
            report = NavJobFeed().enrich([row])
        self.assertEqual(report["matched_profiles"], 0)

    def test_nav_redirect_cannot_send_token_to_another_host(self):
        handler = _NavRedirect()
        request = __import__("urllib.request").request.Request("https://pam-stilling-feed.nav.no/api/v1/feed")
        with self.assertRaisesRegex(ValueError, "escaped"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://attacker.example/collect")


if __name__ == "__main__":
    unittest.main()
