import contextlib
import io
import unittest
from email.message import Message
from unittest.mock import patch

import signalpost_agent as sp


class Response:
    def __init__(self, body: bytes, content_type: str):
        self._body = io.BytesIO(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, size=-1): return self._body.read(size)
    def geturl(self): return "https://example.no/feed.xml" if self.headers.get_content_type() != "text/plain" else "https://example.no/robots.txt"


class ActivityFeedTests(unittest.TestCase):
    def test_parser_discovers_only_rss_or_atom_alternates(self):
        parser = sp.PageParser("https://example.no/")
        parser.feed('<link rel="alternate" type="application/rss+xml" href="/news.xml"><link rel="alternate" type="text/css" href="/style.css">')
        self.assertEqual(parser.feed_links, ["https://example.no/news.xml"])

    def test_dated_same_host_feed_item_becomes_evidence_backed_activity(self):
        homepage = b'<html><head><title>Example AS</title><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body><h1>Example AS</h1><p>Official company</p></body></html>'
        feed = b'<rss><channel><item><title>Example AS opens a new office</title><link>https://example.no/news/office</link><pubDate>Sat, 17 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
        responses = [Response(b"User-agent: *\nAllow: /", "text/plain"), Response(homepage, "text/html"), Response(feed, "application/rss+xml")]
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            env = {"organisation_number": "923609016", "identity_anchor": {"organisation_number": "923609016", "legal_name": "Example AS", "status": "verified"},
                   "claims": [], "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0},
                   "run": {"started_at": "2026-10-18T00:00:00Z"}, "errors": [], "source_snapshots": [], "evidence": [], "changes": []}
            agent = sp.Agent.__new__(sp.Agent)
            agent.timeout = 1
            agent.snapshot = lambda org, family, url, raw, payload: {"id": f"snap-{len(env['source_snapshots'])}", "source_id": f"snap-{len(env['source_snapshots'])}", "family": family, "organisation_number": org, "url": url, "retrieved_at": "2026-10-18T00:00:00Z", "changed": False}
            agent.website("923609016", "https://example.no", "Example AS", env, lambda family, status, facts=None, reason=None: env["claims"].append({"family": family, "status": status, "facts": facts or [], **({"reason": reason} if reason else {})}))
            result = agent.finish(env, 0)
        activity = next(row for row in result["claims"] if row["family"] == "public_activity")
        self.assertEqual(activity["status"], "available", result["errors"])
        self.assertEqual(activity["facts"][0]["value"]["published_at"], "2026-10-17")
        self.assertEqual(activity["facts"][0]["value"]["article_url"], "https://example.no/news/office")
        self.assertEqual(activity["facts"][0]["source_url"], "https://example.no/feed.xml")
        self.assertTrue(activity["facts"][0]["evidence_ids"])
        self.assertFalse(any(error["type"] == "evidence_integrity" for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
