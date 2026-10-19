import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import signalpost_agent as sp
from public_ats import endpoint, fetch_linked_jobs, linked_board, normalize_jobs


class PublicATSTests(unittest.TestCase):
    def test_only_explicit_safe_links_to_supported_board_hosts_are_accepted(self):
        self.assertEqual(linked_board("https://boards.greenhouse.io/nordicco/jobs/123"),
                         {"provider": "greenhouse", "slug": "nordicco", "link": "https://boards.greenhouse.io/nordicco/jobs/123"})
        self.assertEqual(linked_board("https://jobs.eu.lever.co/nordicco/123" )["provider"], "lever_eu")
        self.assertEqual(linked_board("https://jobs.ashbyhq.com/nordicco" )["provider"], "ashby")
        for value in ("http://jobs.lever.co/acme", "https://jobs.lever.co/user:pass@acme",
                      "https://jobs.lever.co/acme:8443", "https://attacker.example/jobs/greenhouse/acme",
                      "https://jobs.ashbyhq.com/../../internal"):
            self.assertIsNone(linked_board(value), value)

    def test_endpoints_are_fixed_provider_hosts(self):
        self.assertEqual(endpoint({"provider": "greenhouse", "slug": "acme"})[1], "boards-api.greenhouse.io")
        self.assertIn("api.eu.lever.co", endpoint({"provider": "lever_eu", "slug": "acme"})[0])
        self.assertEqual(endpoint({"provider": "ashby", "slug": "acme"})[1], "api.ashbyhq.com")

    def test_jobs_require_provider_canonical_https_urls(self):
        jobs = normalize_jobs({"provider": "lever", "slug": "acme"}, [{
            "text": "Engineer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"location": "Oslo"}, "createdAt": "2026-10-15T00:00:00Z"
        }, {"text": "Bad", "hostedUrl": "https://attacker.example/job", "categories": {}}], "https://jobs.lever.co/acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Engineer")
        self.assertEqual(jobs[0]["location"], "Oslo")
        self.assertEqual(jobs[0]["published_at"], "2026-10-15T00:00:00Z")

    def test_fetch_is_limited_to_distinct_linked_boards_and_reports_failures(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return ([{"text": "SRE", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {}}], b"[]", 200)
        result = fetch_linked_jobs(["https://jobs.lever.co/acme", "https://jobs.lever.co/acme/jobs",
                                    "https://boards.greenhouse.io/other/jobs"], fetch, max_boards=1)
        self.assertEqual(result["boards_checked"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["jobs"][0]["title"], "SRE")

    def test_verified_company_page_link_is_preserved_with_api_job_evidence(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        robots = Response("https://example.no/robots.txt", b"User-agent: *\nAllow: /\n")
        homepage = Response("https://example.no/", b'<title>Nordic Coffee</title><p>NORDIC COFFEE ROASTERS AS</p><a href="https://boards.greenhouse.io/nordiccoffee">Careers</a><a href="https://www.linkedin.com/company/nordic-coffee">LinkedIn</a><a href="https://linkedin.com.evil.example/company/fake">Fake</a>')
        api_payload = {"jobs": [{"id": 12, "title": "Roaster", "location": {"name": "Oslo"},
                                  "absolute_url": "https://boards.greenhouse.io/nordiccoffee/jobs/12", "updated_at": "2026-10-15T00:00:00Z"}]}
        env = {"organisation_number": "923609016", "identity_anchor": {"status": "verified"},
               "operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[robots, homepage]), \
             patch.object(sp, "get_json", return_value=(api_payload, json.dumps(api_payload).encode(), 200)):
            scratch = Path.cwd() / "work"
            scratch.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=scratch) as temp:
                self_agent = sp.Agent(Path(temp) / "state.sqlite3", website=True, timeout=2)
                try:
                    self_agent.website("923609016", "https://example.no", "NORDIC COFFEE ROASTERS AS", env,
                                       lambda family, status, facts=None, reason=None: claims.append({"family": family, "status": status, "facts": facts or [], "reason": reason}))
                finally:
                    self_agent.db.close()
        jobs_claim = next(claim for claim in claims if claim["family"] == "jobs")
        web_claim = next(claim for claim in claims if claim["family"] == "web_presence")
        self.assertEqual(jobs_claim["status"], "available")
        self.assertEqual(jobs_claim["facts"][0]["value"]["title"], "Roaster")
        self.assertEqual(jobs_claim["facts"][0]["value"]["source_updated_at"], "2026-10-15T00:00:00Z")
        evidence_ids = jobs_claim["facts"][0]["evidence_ids"]
        self.assertEqual(len(evidence_ids), 2)
        snaps = {item["id"]: item for item in env["source_snapshots"]}
        self.assertEqual({snaps[eid]["family"] for eid in evidence_ids}, {"jobs"})
        profile_facts = [fact for fact in web_claim["facts"] if fact["field"] == "company_profile_url"]
        self.assertEqual(profile_facts[0]["value"], {"platform": "LinkedIn", "url": "https://www.linkedin.com/company/nordic-coffee"})
        self.assertEqual(env["operations"]["requests"], 3)


if __name__ == "__main__":
    unittest.main()
