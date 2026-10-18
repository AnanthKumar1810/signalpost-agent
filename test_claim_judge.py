import json
import unittest
from unittest.mock import patch

from claim_judge import WebSearchJudge
import signalpost_agent as sp


class JudgeAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.judge = WebSearchJudge(api_key="test", max_calls=1)

    def test_rejects_cross_entity_even_with_confident_quote(self):
        candidate = {"field":"active_job", "value":"CEO", "source_url":"https://wrong.no/job",
                     "quote":"Apply now for the CEO position with our company and team.",
                     "entity_org_number":"999999999", "entity_name":"Wrong AS"}
        self.assertEqual(self.judge._reject_reason(candidate,"923609016","Right AS"),"entity_mismatch")

    def test_rejects_missing_quote_http_and_bad_shapes(self):
        base = {"field":"news", "value":"new office", "source_url":"https://right.no/news",
                "quote":"A substantive passage that gives an actual claim.",
                "entity_org_number":"923609016"}
        self.assertEqual(self.judge._reject_reason({**base,"quote":"short"},"923609016","Right AS"),"missing_substantive_quote")
        self.assertEqual(self.judge._reject_reason({**base,"source_url":"http://right.no"},"923609016","Right AS"),"source_not_https")
        self.assertEqual(self.judge._reject_reason({**base,"value":[]},"923609016","Right AS"),"invalid_claim_shape")
        self.assertEqual(self.judge._reject_reason("malicious output","923609016","Right AS"),"not_an_object")

    def test_exact_entity_candidate_is_marked_unverified_and_budgeted(self):
        response = {"output":[{"type":"message","content":[{"type":"output_text","text":json.dumps({"facts":[
            {"field":"active_job_title","value":"Engineer","source_url":"https://right.no/jobs/eng",
             "quote":"Apply now for this engineering position at Right AS.","as_of":"2026-10-17","entity_org_number":"923609016"}]})}]}]}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return json.dumps(response).encode()
        with patch("urllib.request.urlopen",return_value=FakeResponse()):
            result=self.judge.discover("923609016","Right AS")
            exhausted=self.judge.discover("923609016","Right AS")
        self.assertEqual(result["status"],"available")
        self.assertIn("candidate_only",result["facts"][0]["verification"])
        self.assertEqual(exhausted["status"],"budget_exhausted")

    def test_malformed_model_json_fails_closed(self):
        response = {"output":[{"type":"message","content":[{"type":"output_text","text":"not json"}]}]}
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return json.dumps(response).encode()
        with patch("urllib.request.urlopen",return_value=FakeResponse()):
            result=self.judge.discover("923609016","Right AS")
        self.assertEqual(result["status"],"failed")
        self.assertEqual(result["facts"],[])

    def test_cited_page_must_match_quote_entity_and_publication_date(self):
        class Headers:
            def __init__(self, content_type): self.content_type = content_type
            def get_content_type(self): return self.content_type
        class Response:
            def __init__(self, body, kind):
                self.body, self.kind = body, kind
                self.headers = Headers("text/plain" if kind == "robots" else "text/html")
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://right.no/robots.txt" if self.kind == "robots" else "https://right.no/news"
            def read(self, limit): return self.body
        result = {"facts": [{"field": "news", "value": "new office", "source_url": "https://right.no/news",
                            "quote": "Right AS opened a new office in Oslo.", "as_of": "2026-10-18",
                            "entity_org_number": "923609016", "entity_name": "Right AS"}]}
        robots = b"User-agent: *\nAllow: /\n"
        page = b'<meta property="article:published_time" content="2026-10-18T09:00:00Z"><title>Right AS news</title><h1>Right AS</h1><p>Right AS opened a new office in Oslo.</p>'
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[Response(robots, "robots"), Response(page, "page")]):
            checked = self.judge.verify_citations(result, "923609016", "Right AS")
        self.assertEqual(checked["status"], "verified")
        self.assertEqual(len(checked["verified_facts"]), 1)
        self.assertEqual(checked["verification_requests"], 2)

    def test_cited_page_quote_mismatch_is_rejected(self):
        class Headers:
            def __init__(self, body): self.body = body
            def get_content_type(self): return "text/plain" if self.body.startswith(b"User-agent") else "text/html"
        class Response:
            def __init__(self, body): self.body = body; self.headers = Headers(body)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://right.no/"
            def read(self, limit): return self.body
        result = {"facts": [{"field": "news", "value": "opening", "source_url": "https://right.no/news",
                            "quote": "Right AS opened a new office in Oslo.", "as_of": "2026-10-18",
                            "entity_org_number": "923609016", "entity_name": "Right AS"}]}
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[Response(b"User-agent: *\nAllow: /\n"), Response(b"<title>Right AS</title><p>Unrelated content only.</p>")]):
            checked = self.judge.verify_citations(result, "923609016", "Right AS")
        self.assertEqual(checked["status"], "not_available")
        self.assertIn("quote was not found", checked["rejected"][0]["reason"])


if __name__ == "__main__": unittest.main()
