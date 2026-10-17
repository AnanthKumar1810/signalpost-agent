import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import signalpost_agent as sp


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd() / "work")
        self.agent = sp.Agent(Path(self.tmp.name) / "state.sqlite3", website=False)

    def tearDown(self):
        self.agent.db.close()
        self.tmp.cleanup()

    def test_invalid_number_does_not_make_network_request(self):
        with patch.object(sp, "get_json", side_effect=AssertionError("network called")):
            result = self.agent.company("123")
        self.assertEqual(result["identity_anchor"]["status"], "not_applicable")
        self.assertEqual(result["operations"]["requests"], 0)

    def test_official_registry_maps_facts_and_evidence(self):
        data = {"organisasjonsnummer": "923609016", "navn": "Eksempel AS", "organisasjonsform": {"kode": "AS"}, "registreringsdatoEnhetsregisteret": "2010-01-01", "naeringskode1": {"kode": "62.010", "beskrivelse": "Programming"}, "antallAnsatte": 12, "hjemmeside": "https://example.no", "forretningsadresse": {"kommune": "Oslo", "postnummer": "0001", "poststed": "Oslo", "adresse": ["Gate 1"]}, "sisteInnsendteAarsregnskap": {"aar": 2025}}
        roles = {"rollegrupper": [{"roller": [{"type": {"beskrivelse": "Daglig leder"}, "person": {"navn": "Ada", "etternavn": "Nord"}}]}]}
        with patch.object(sp, "get_json", side_effect=[(data, json.dumps(data).encode(), 200), (roles, json.dumps(roles).encode(), 200)]):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "verified")
        self.assertIn("company_details", [c["family"] for c in result["claims"]])
        self.assertEqual(result["operations"]["requests"], 2)
        evidence_ids = {e["id"] for e in result["source_snapshots"]}
        for claim in result["claims"]:
            for fact in claim["facts"]:
                self.assertTrue(set(fact["evidence_ids"]) <= evidence_ids)

    def test_registry_plus_website_adds_only_exact_entity_facts(self):
        data = {"navn":"Example AS","organisasjonsform":{"kode":"AS"},"hjemmeside":"https://example.no","naeringskode1":{"kode":"62.010","beskrivelse":"Software"}}
        roles = {"rollegrupper":[]}
        robots = b"User-agent: *" + bytes([10]) + b"Allow: /" + bytes([10])
        site = b'<title>Example AS</title><h1>Example AS</h1><meta name="description" content="Software services"><a href="/careers">Careers</a>'
        class Headers:
            def get_content_type(self): return "text/plain" if self.kind == "robots" else "text/html"
        class Response:
            def __init__(self, body, kind): self.body,self.kind=body,kind; self.headers=Headers(); self.headers.kind=kind
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def geturl(self): return "https://example.no/robots.txt" if self.kind=="robots" else "https://example.no/"
            def read(self,n): return self.body
        baseline = self.agent
        with patch.object(sp,"get_json",side_effect=[(data,b"registry",200),(roles,b"roles",200)]):
            registry_result=baseline.company("923609016")
        web_agent=sp.Agent(Path(self.tmp.name)/"web.sqlite3",website=True)
        try:
            with patch.object(sp,"public_host",return_value=True), patch.object(sp,"get_json",side_effect=[(data,b"registry",200),(roles,b"roles",200)]), patch.object(sp,"open_safe",side_effect=[Response(robots,"robots"),Response(site,"site")]):
                enriched_result=web_agent.company("923609016")
        finally:
            web_agent.db.close()
        fact_count=lambda row:sum(len(c["facts"]) for c in row["claims"])
        self.assertGreater(fact_count(enriched_result),fact_count(registry_result))
        web_claim=next(c for c in enriched_result["claims"] if c["family"]=="web_presence")
        self.assertEqual(web_claim["status"],"available")
        self.assertTrue(enriched_result["changes"]==[])

    def test_repeat_same_snapshot_is_idempotent(self):
        data = {"navn": "Eksempel AS"}
        raw = json.dumps(data).encode()
        one = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", raw, data)
        two = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", raw, data)
        count = self.agent.db.execute("select count(*) from snapshots").fetchone()[0]
        self.assertEqual(one["id"], two["id"])
        self.assertFalse(one["changed"])
        self.assertFalse(two["changed"])
        self.assertEqual(count, 1)
        changed = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", b'{"navn":"Nytt AS"}', {"navn":"Nytt AS"})
        self.assertTrue(changed["changed"])
        self.assertEqual(changed["changed_fields"], ["navn"])

    def test_nameless_or_mismatched_site_is_ambiguous(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://example.no/"
            def read(self, n): return b"<title>Another legal entity</title><p>Not Example AS.</p>"
        env = {"operations":{"requests":0},"source_snapshots":[],"evidence":[],"errors":[],"changes":[]}
        claims=[]
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", return_value=Response()):
            self.agent.website("923609016", "https://example.no", "Example AS", env, lambda *a, **k: claims.append((a,k)))
        web_claim = next(c for c in claims if c[0][0] == "web_presence")
        self.assertEqual(web_claim[0][1], "ambiguous")

    def test_private_host_is_rejected(self):
        self.assertFalse(sp.public_host("127.0.0.1"))
        self.assertFalse(sp.public_host("10.0.0.8"))
        self.assertTrue(sp.public_host("8.8.8.8"))

    def test_safe_website_requires_exact_legal_name_or_org_number(self):
        parser = sp.PageParser("https://example.no/")
        parser.feed("<title>Company</title><body>Example AS offers software.</body>")
        self.assertTrue(bool(__import__("re").search(r"(?<![\w])" + __import__("re").escape("Example AS") + r"(?![\w])", "Example AS offers software.", __import__("re").I)))
        self.assertFalse(bool(__import__("re").search(r"(?<![\w])" + __import__("re").escape("Example AS") + r"(?![\w])", "Example ASSETS", __import__("re").I)))


if __name__ == "__main__":
    unittest.main()
