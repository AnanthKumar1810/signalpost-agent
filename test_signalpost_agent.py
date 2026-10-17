import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import signalpost_agent as sp


class AgentTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.cwd() / "work"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.agent = sp.Agent(Path(self.tmp.name) / "state.sqlite3", website=False)

    def tearDown(self):
        self.agent.db.close()
        self.tmp.cleanup()

    def test_invalid_number_does_not_make_network_request(self):
        with patch.object(sp, "get_json", side_effect=AssertionError("network called")):
            result = self.agent.company("123")
        self.assertEqual(result["identity_anchor"]["status"], "not_applicable")
        self.assertEqual(result["operations"]["requests"], 0)
        self.assertEqual({c["status"] for c in result["claims"]}, {"not_applicable"})
        self.assertEqual({c["family"] for c in result["claims"]}, set(sp.FAMILIES))

    def test_financial_data_for_a_different_entity_is_not_published(self):
        payload = {"regnskap": [{"virksomhet": {"organisasjonsnummer": "999999999"}, "resultatregnskapResultat": {"aarsresultat": 999}}]}
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)):
            env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials("923609016", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        financial = next(item for item in claims if item[0][0] == "financials")
        self.assertEqual(financial[0][1], "not_available")
        self.assertEqual(financial[0][2], [])

    def test_100_company_batch_has_one_complete_terminal_result_per_input(self):
        def make_org(seed):
            first_eight = f"{seed:08d}"
            check = (11 - sum(int(d) * w for d, w in zip(first_eight, (3, 2, 7, 6, 5, 4, 3, 2))) % 11) % 11
            return first_eight + str(check) if check < 10 else None
        orgs = [value for seed in range(10_000_000, 10_000_150) if (value := make_org(seed))][:100]
        self.assertEqual(len(orgs), 100)
        def fixture(url, timeout):
            if "/roller" in url:
                data = {"rollegrupper": []}
            elif "regnskapsregisteret" in url:
                org = url.split("orgNummer=")[-1]
                data = {"regnskap": [{"virksomhet": {"organisasjonsnummer": org}, "regnskapsperiode": {"tilDato": "2024-12-31"}, "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 100}}}}]}
            else:
                org = url.rsplit("/", 1)[-1]
                data = {"navn": f"Fixture {org}", "organisasjonsnummer": org, "organisasjonsform": {"kode": "AS"}, "naeringskode1": {"beskrivelse": "Testing"}}
            raw = json.dumps(data, sort_keys=True).encode()
            return data, raw, 200
        with patch.object(sp, "get_json", side_effect=fixture):
            results = [self.agent.company(org) for org in orgs]
        self.assertEqual(len(results), 100)
        self.assertEqual([row["organisation_number"] for row in results], orgs)
        self.assertTrue(all(row["run"]["status"] == "complete" for row in results))
        self.assertTrue(all({claim["family"] for claim in row["claims"]} == set(sp.FAMILIES) for row in results))
        self.assertTrue(all(all(claim["status"] in sp.STATES for claim in row["claims"]) for row in results))

    def test_profile_dashboard_is_generated_and_escapes_untrusted_text(self):
        source = Path(self.tmp.name) / "profiles.jsonl"
        source.write_text(json.dumps({"organisation_number": "923609016", "identity_anchor": {"legal_name": "Fixture AS"}, "summary": {"text": "Safe summary"}, "claims": [], "source_snapshots": [], "errors": [{"message": "</script><script>bad()</script>"}]}, ensure_ascii=False) + "\n", encoding="utf-8")
        dashboard = sp.make_dashboard(source)
        page = dashboard.read_text(encoding="utf-8")
        self.assertIn("Signalpost company profiles", page)
        self.assertIn("Safe summary", page)
        self.assertNotIn("</script><script>bad()", page)

    def test_official_registry_maps_facts_and_evidence(self):
        data = {"organisasjonsnummer": "923609016", "navn": "Eksempel AS", "organisasjonsform": {"kode": "AS"}, "registreringsdatoEnhetsregisteret": "2010-01-01", "naeringskode1": {"kode": "62.010", "beskrivelse": "Programming"}, "antallAnsatte": 12, "hjemmeside": "https://example.no", "forretningsadresse": {"kommune": "Oslo", "postnummer": "0001", "poststed": "Oslo", "adresse": ["Gate 1"]}, "sisteInnsendteAarsregnskap": {"aar": 2025}}
        roles = {"rollegrupper": [{"roller": [{"type": {"beskrivelse": "Daglig leder"}, "person": {"navn": "Ada", "etternavn": "Nord"}}]}]}
        annual = {"_embedded": {"regnskap": [{"virksomhet": {"organisasjonsnummer": "923609016"}, "valuta": "NOK", "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}, "resultatregnskapResultat": {"aarsresultat": 125000, "driftsresultat": {"driftsresultat": 150000, "driftsinntekter": {"sumDriftsinntekter": 750000}}}, "eiendeler": {"sumEiendeler": 500000}, "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 325000}}}]}}
        with patch.object(sp, "get_json", side_effect=[(data, json.dumps(data).encode(), 200), (roles, json.dumps(roles).encode(), 200), (annual, json.dumps(annual).encode(), 200)]):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "verified")
        self.assertIn("company_details", [c["family"] for c in result["claims"]])
        self.assertEqual(result["operations"]["requests"], 3)
        evidence_ids = {e["id"] for e in result["source_snapshots"]}
        for claim in result["claims"]:
            for fact in claim["facts"]:
                self.assertTrue(set(fact["evidence_ids"]) <= evidence_ids)
        financials = next(c for c in result["claims"] if c["family"] == "financials")
        self.assertEqual(financials["status"], "available")
        self.assertIn(("revenue_nok", 750000), [(fact["field"], fact["value"]) for fact in financials["facts"]])
        self.assertEqual({c["family"] for c in result["claims"]}, set(sp.FAMILIES))
        self.assertIn("Eksempel AS", result["summary"]["text"])

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
        annual = {"_embedded": {"regnskap": []}}
        with patch.object(sp,"get_json",side_effect=[(data,b"registry",200),(roles,b"roles",200),(annual,b"annual",200)]):
            registry_result=baseline.company("923609016")
        web_agent=sp.Agent(Path(self.tmp.name)/"web.sqlite3",website=True)
        try:
            with patch.object(sp,"public_host",return_value=True), patch.object(sp,"get_json",side_effect=[(data,b"registry",200),(roles,b"roles",200),(annual,b"annual",200)]), patch.object(sp,"open_safe",side_effect=[Response(robots,"robots"),Response(site,"site")]):
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
