import json
import io
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import signalpost_agent as sp


class CandidateVerificationIntegrationTests(unittest.TestCase):
    def test_verified_discovery_returns_the_enriched_profile_for_output(self):
        class FakeAgent:
            def website(self, org, url, legal_name, profile, add_claim, require_domain_name_match=False):
                self.assert_gate = require_domain_name_match
                profile.setdefault("operations", {})["requests"] += 1
                add_claim("web_presence", "available", [{"field": "website", "value": url}])

        original = {"organisation_number": "923609016", "claims": [], "operations": {"requests": 2}}
        enriched, requests, verified, ambiguous, blocked = sp._verify_discovered_website_candidates(
            FakeAgent(), original, "923609016", "Nordic Coffee AS", ["https://nordiccoffee.no"])
        self.assertTrue(verified)
        self.assertEqual(requests, 1)
        self.assertFalse(ambiguous)
        self.assertFalse(blocked)
        self.assertIsNot(enriched, original)
        self.assertEqual(enriched["claims"][0]["facts"][0]["value"], "https://nordiccoffee.no")


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

    def test_failed_identity_summary_does_not_claim_registration(self):
        with patch.object(sp, "get_json", side_effect=OSError("offline")):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "failed")
        self.assertIn("could not be verified", result["summary"]["text"])
        self.assertEqual(result["summary"]["evidence_ids"], [])

    def test_finish_does_not_replace_an_accepted_llm_summary(self):
        env = {"organisation_number": "923609016", "identity_anchor": {"status": "verified", "legal_name": "Example AS"},
               "claims": [], "evidence": [], "source_snapshots": [], "changes": [], "errors": [],
               "operations": {"requests": 0, "runtime_ms": 0}, "run": {},
               "summary": {"text": "Accepted LLM prose.", "method": "optional LLM summary; generated from verified fact catalog"}}
        result = self.agent.finish(env, 0.0)
        self.assertEqual(result["summary"]["text"], "Accepted LLM prose.")

    def test_common_display_spacing_and_hyphens_are_normalized(self):
        self.assertTrue(sp.valid_org("923 609 016"))
        self.assertTrue(sp.valid_org(" 923-609-016 "))
        with patch.object(sp, "get_json", side_effect=AssertionError("network called")):
            result = self.agent.company(" 923 609 016 ")
        self.assertEqual(result["organisation_number"], "923609016")
        self.assertEqual(result["identity_anchor"]["organisation_number"], "923609016")

    def test_financial_data_for_a_different_entity_is_not_published(self):
        payload = {"regnskap": [{"virksomhet": {"organisasjonsnummer": "999999999"}, "resultatregnskapResultat": {"aarsresultat": 999}}]}
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)):
            env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials("923609016", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        financial = next(item for item in claims if item[0][0] == "financials")
        self.assertEqual(financial[0][1], "not_available")
        self.assertEqual(financial[0][2], [])

    def test_financial_unsupported_ideell_plan_is_not_applicable(self):
        body = b'{"message":"Regnskapet inneholder en oppstillingsplan som ikke er stottet (IDEELL)"}'
        exc = urllib.error.HTTPError(f"{sp.ACCOUNTS}/987441623", 500, "Internal Server Error", {}, io.BytesIO(body))
        with patch.object(sp, "get_json", side_effect=exc):
            env = {"identity_anchor": {"organisation_form": "STI"}, "operations": {"requests": 0},
                   "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials("987441623", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        financial = next(item for item in claims if item[0][0] == "financials")
        self.assertEqual(financial[0][1], "not_applicable")
        self.assertIn("IDEELL accounting presentation plan", financial[1]["reason"])
        self.assertIn("IDEELL", env["errors"][0]["response_message"])

    def test_generic_financial_http_500_remains_failed(self):
        exc = urllib.error.HTTPError(f"{sp.ACCOUNTS}/987441623", 500, "Internal Server Error", {}, io.BytesIO(b'{"message":"internal server error"}'))
        with patch.object(sp, "get_json", side_effect=exc):
            env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials("987441623", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        financial = next(item for item in claims if item[0][0] == "financials")
        self.assertEqual(financial[0][1], "failed")
        self.assertIn("HTTP 500", financial[1]["reason"])

    def test_accounts_lookup_uses_brreg_production_not_preview(self):
        self.assertEqual(sp.ACCOUNTS, "https://data.brreg.no/regnskapsregisteret/regnskap")
        self.assertNotIn(".ppe.", sp.ACCOUNTS)

    def test_accounts_lookup_uses_documented_org_number_path(self):
        payload = {"virksomhet": {"organisasjonsnummer": "923609016"}, "valuta": "NOK", "regnskapsperiode": {"tilDato": "2025-12-31"}, "resultatregnskapResultat": {"aarsresultat": 100}}
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)) as get_json:
            env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials("923609016", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        get_json.assert_called_once()
        self.assertEqual(get_json.call_args.args[0], f"{sp.ACCOUNTS}/923609016")
        financial = next(call[0] for call in claims if call[0][0] == "financials")
        self.assertEqual(financial[1], "available")

    def test_1000_company_batch_has_one_complete_terminal_result_per_input(self):
        def make_org(seed):
            first_eight = f"{seed:08d}"
            check = (11 - sum(int(d) * w for d, w in zip(first_eight, (3, 2, 7, 6, 5, 4, 3, 2))) % 11) % 11
            return first_eight + str(check) if check < 10 else None
        orgs = [value for seed in range(10_000_000, 10_002_000) if (value := make_org(seed))][:1000]
        self.assertEqual(len(orgs), 1000)
        def fixture(url, timeout):
            if "/roller" in url:
                data = {"rollegrupper": []}
            elif "regnskapsregisteret" in url:
                org = url.rstrip("/").rsplit("/", 1)[-1]
                data = {"regnskap": [{"virksomhet": {"organisasjonsnummer": org}, "regnskapsperiode": {"tilDato": "2024-12-31"}, "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 100}}}}]}
            else:
                org = url.rsplit("/", 1)[-1]
                data = {"navn": f"Fixture {org}", "organisasjonsnummer": org, "organisasjonsform": {"kode": "AS"}, "naeringskode1": {"beskrivelse": "Testing"}}
            raw = json.dumps(data, sort_keys=True).encode()
            return data, raw, 200
        with patch.object(sp, "get_json", side_effect=fixture):
            results = [self.agent.company(org) for org in orgs]
        self.assertEqual(len(results), 1000)
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
        self.assertIn("Compare selected", page)
        self.assertIn("Filter by result status", page)
        self.assertIn("max-height", page)
        self.assertIn("Safe summary", page)
        self.assertNotIn("</script><script>bad()", page)

    def test_official_registry_maps_facts_and_evidence(self):
        data = {"organisasjonsnummer": "923609016", "navn": "Eksempel AS", "organisasjonsform": {"kode": "AS"}, "registreringsdatoEnhetsregisteret": "2010-01-01", "naeringskode1": {"kode": "62.010", "beskrivelse": "Programming"}, "aktivitet": ["Software development", "and support"], "antallAnsatte": 12, "hjemmeside": "https://example.no", "forretningsadresse": {"kommune": "Oslo", "postnummer": "0001", "poststed": "Oslo", "adresse": ["Gate 1"]}, "sisteInnsendteAarsregnskap": {"aar": 2025}}
        roles = {"rollegrupper": [{"roller": [{"type": {"beskrivelse": "Daglig leder"}, "person": {"navn": {"fornavn": "Ada", "etternavn": "Nord"}}}]}]}
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
        self.assertIn("registered activity as Programming", result["summary"]["text"])
        self.assertIn("declared activity as Software development and support", result["summary"]["text"])
        self.assertIn(("declared_activity", "Software development and support"), [(fact["field"], fact["value"]) for fact in next(c for c in result["claims"] if c["family"] == "company_details")["facts"]])
        self.assertIn("12 registered employees", result["summary"]["text"])
        self.assertIn("revenue NOK 750,000", result["summary"]["text"])
        self.assertIn("net result NOK 125,000", result["summary"]["text"])
        self.assertIn("Daglig leder: Ada Nord", result["summary"]["text"])
        self.assertTrue(result["summary"]["evidence_ids"])
        people = next(c for c in result["claims"] if c["family"] == "people")
        self.assertEqual(people["facts"][0]["value"]["name"], "Ada Nord")

    def test_additional_official_registry_fields_have_identity_tied_evidence(self):
        entity = {
            "organisasjonsnummer": "923609016", "navn": "Eksempel AS",
            "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
            "stiftelsesdato": "2018-05-03", "registreringsdatoForetaksregisteret": "2018-05-10",
            "vedtektsfestetFormaal": ["Produksjon av utstyr", "og rådgivning."],
            "institusjonellSektorkode": {"kode": "2100", "beskrivelse": "Private aksjeselskaper mv."},
            "kapital": {"type": "Aksjekapital", "valuta": "NOK", "belop": 30000.0,
                        "antallAksjer": 100, "innfortDato": "2018-05-03"},
            "registrertIMvaregisteret": False, "sisteInnsendteAarsregnskap": "2025",
            "epostadresse": "post@eksempel.no", "telefon": "12345678",
            "postadresse": {"adresse": ["Postboks 1"], "postnummer": "0001", "poststed": "Oslo"},
        }
        payloads = [entity, {"rollegrupper": []}, []]
        with patch.object(sp, "get_json", side_effect=[(item, json.dumps(item).encode(), 200) for item in payloads]):
            result = self.agent.company("923609016")
        self.assertEqual(result["operations"]["requests"], 3)
        details = next(c for c in result["claims"] if c["family"] == "company_details")
        by_field = {fact["field"]: fact for fact in details["facts"]}
        self.assertEqual(by_field["incorporated_at"]["valid_at"], "2018-05-03")
        self.assertEqual(by_field["stated_purpose"]["value"], "Produksjon av utstyr og rådgivning.")
        self.assertEqual(by_field["share_capital_nok"]["value"], 30000.0)
        self.assertEqual(by_field["share_capital_nok"]["valid_at"], "2018-05-03")
        self.assertEqual(by_field["vat_registered"]["value"], False)
        self.assertEqual(by_field["contact_email"]["value"], "post@eksempel.no")
        self.assertEqual(by_field["latest_submitted_accounts_year"]["value"], "2025")
        self.assertEqual(by_field["mailing_address"]["evidence_ids"], [result["identity_anchor"]["evidence_id"]])
        locations = next(c for c in result["claims"] if c["family"] == "locations")
        self.assertEqual(locations["status"], "available")
        self.assertEqual([fact["field"] for fact in locations["facts"]], ["mailing_address"])

    def test_registry_plus_website_adds_only_exact_entity_facts(self):
        data = {"organisasjonsnummer":"923609016","navn":"Example AS","organisasjonsform":{"kode":"AS"},"hjemmeside":"https://example.no","naeringskode1":{"kode":"62.010","beskrivelse":"Software"}}
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

    def test_same_host_current_job_page_requires_application_action_and_has_evidence(self):
        class Headers:
            def __init__(self, kind): self.kind = kind
            def get_content_type(self): return "text/plain" if self.kind == "robots" else "text/html"
        class Response:
            def __init__(self, body, url, kind): self.body,self.url,self.headers=body,url,Headers(kind)
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def geturl(self): return self.url
            def read(self,n): return self.body
        robots=Response(b"User-agent: *\\nAllow: /\\n","https://example.no/robots.txt","robots")
        home=Response(b'<title>Example AS</title><h1>Example AS</h1><a href="/jobs/software-engineer">Software Engineer</a>',"https://example.no/","html")
        job=Response(b"<title>Software Engineer</title><h1>Software Engineer</h1><p>Apply now for this role.</p>","https://example.no/jobs/software-engineer","html")
        env={"operations":{"requests":0},"source_snapshots":[],"evidence":[],"errors":[],"changes":[]}
        claims=[]
        with patch.object(sp,"public_host",return_value=True), patch.object(sp,"open_safe",side_effect=[robots,home,job]):
            self.agent.website("923609016","https://example.no","Example AS",env,lambda *a,**k:claims.append((a,k)))
        jobs=next(row for row in claims if row[0][0]=="jobs")
        self.assertEqual(jobs[0][1],"available")
        self.assertEqual(jobs[0][2][0]["field"],"advertised_job_title")
        self.assertEqual(jobs[0][2][0]["value"],{"title":"Software Engineer","freshness":"unconfirmed"})
        self.assertTrue(jobs[0][2][0]["evidence_ids"])

    def test_dated_job_page_can_be_called_active(self):
        self.assertTrue(sp.is_generic_careers_title("Ledige stillinger"))
        self.assertTrue(sp.is_generic_careers_title("Job Openings"))
        self.assertFalse(sp.is_generic_careers_title("Software Engineer"))
        result = sp.classify_job_page(
            "Published 2026-10-01. Apply now for this role.", [],
            reference_date=__import__("datetime").date(2026, 10, 19),
        )
        self.assertEqual(result, {"field":"active_job_title","freshness":"confirmed","valid_at":"2026-10-01"})

    def test_expired_or_stale_job_page_is_not_called_current(self):
        today = __import__("datetime").date(2026, 10, 19)
        self.assertIsNone(sp.classify_job_page("Application deadline 2026-10-01. Applications are closed. Apply now.", [], today))
        self.assertIsNone(sp.classify_job_page("Published 2026-09-01. Application deadline 2026-10-01. Apply now.", [], today))
        stale = sp.classify_job_page("Published 2020-01-01. Apply now.", [], today)
        self.assertEqual(stale["field"], "advertised_job_title")
        self.assertEqual(stale["freshness"], "unconfirmed")

    def test_website_http_error_terminates_all_site_families(self):
        class Headers:
            def get_content_type(self): return "text/plain"
        class RobotsResponse:
            headers = Headers()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://example.no/robots.txt"
            def read(self, size): return b"User-agent: *\nAllow: /\n"
        env = {"operations": {"requests": 0}, "errors": [], "source_snapshots": [], "changes": []}
        claims = []
        def claim(family, status, facts=None, reason=None):
            claims.append({"family": family, "status": status, "facts": facts or [], "reason": reason})
        missing_page = urllib.error.HTTPError("https://example.no/", 404, "not found", {}, None)
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[RobotsResponse(), missing_page]):
            self.agent.website("923609016", "https://example.no", "Example AS", env, claim)
        self.assertEqual({item["family"] for item in claims}, {"web_presence", "jobs", "public_activity"})
        self.assertTrue(all(item["status"] == "not_available" for item in claims))

    def test_repeat_snapshot_is_idempotent_and_tracks_source_revisions_independently(self):
        data = {"navn": "Eksempel AS"}
        raw = json.dumps(data).encode()
        one = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", raw, data)
        two = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", raw, data)
        count = self.agent.db.execute("select count(*) from snapshots").fetchone()[0]
        self.assertEqual(one["id"], two["id"])
        self.assertFalse(one["changed"])
        self.assertFalse(two["changed"])
        self.assertEqual(count, 1)
        first = self.agent.snapshot("923609016", "jobs", "https://jobs.example/one", b"one", {"title": "Engineer"})
        second = self.agent.snapshot("923609016", "jobs", "https://jobs.example/two", b"two", {"title": "Designer"})
        self.assertFalse(first["changed"])
        self.assertFalse(second["changed"])
        revision = self.agent.snapshot("923609016", "jobs", "https://jobs.example/one", b"one-new", {"title": "Senior Engineer"})
        self.assertTrue(revision["changed"])
        self.assertEqual(revision["changed_fields"], ["title"])
        stored = self.agent.db.execute("select source_url,payload from snapshots where source_key like 'https://jobs.example/one%'").fetchall()
        self.assertTrue(all(row[0] == "https://jobs.example/one" for row in stored))
        self.assertTrue(any("Senior Engineer" in row[1] for row in stored))
        changed = self.agent.snapshot("923609016", "registry", "https://data.brreg.no/x", b'{"navn":"Nytt AS"}', {"navn":"Nytt AS"})
        self.assertTrue(changed["changed"])
        self.assertEqual(changed["changed_fields"], ["navn"])

    def test_old_snapshot_database_is_migrated_without_dropping_history(self):
        path = Path(self.tmp.name) / "legacy.sqlite3"
        db = sqlite3.connect(path)
        db.execute("create table snapshots(org text, family text, digest text, fetched_at text, payload text, primary key(org,family,digest))")
        db.execute("insert into snapshots values('923609016','registry','oldhash','2026-01-01T00:00:00Z','{}')")
        db.execute("create table current_sources(org text, family text, digest text, fetched_at text, payload text, primary key(org,family))")
        db.execute("insert into current_sources values('923609016','registry','oldhash','2026-01-01T00:00:00Z','{}')")
        db.commit()
        db.close()
        migrated = sp.Agent(path, website=False)
        try:
            self.assertEqual(migrated.db.execute("select count(*) from snapshots where source_key='legacy'").fetchone()[0], 1)
            self.assertEqual(migrated.db.execute("select count(*) from current_sources where source_key='legacy'").fetchone()[0], 1)
            migrated.snapshot("923609016", "registry", "https://data.brreg.no/enheter/923609016", b"new", {"name": "Example AS"})
            self.assertEqual(migrated.db.execute("select count(*) from snapshots").fetchone()[0], 2)
        finally:
            migrated.db.close()
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

    def test_suffix_free_brand_is_not_identity_but_full_legal_name_is(self):
        self.assertFalse(sp.legal_name_matches_page(
            "NORDIC COFFEE ROASTERS AS",
            "Welcome. Company details: Nordic Coffee Roasters. Org information and contact details.",
        ))
        self.assertTrue(sp.legal_name_matches_page(
            "NORDIC COFFEE ROASTERS AS", "Legal entity: Nordic Coffee Roasters AS."))
        self.assertFalse(sp.legal_name_matches_page(
            "Example AS", "Another legal entity. This is not Example AS.", "Another legal entity",
        ))
        self.assertFalse(sp.legal_name_matches_page(
            "NORDIC COFFEE ROASTERS AS", "Nordic Coffee Academic Partnership", "Nordic Coffee Academic Partnership",
        ))
        self.assertFalse(sp.legal_name_matches_page("DIPS AS", "DIPS", "DIPS"))
        self.assertFalse(sp.legal_name_matches_page("DIPS AS", "About DIPS and how DIPS supports hospitals."))
        self.assertFalse(sp.legal_name_matches_page("DIPS AS", "We work with DIPS."))

    def test_website_collector_accepts_exact_org_number_when_brand_name_differs(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        robots = Response("https://example.no/robots.txt", b"User-agent: *\\nAllow: /\\n")
        homepage = Response(
            "https://example.no/",
            b"<title>Nordic Roast</title><p>Org.nr: 923 609 016</p><p>Nordic Coffee Roasters.</p>",
        )
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[robots, homepage]):
            self.agent.website("923609016", "https://example.no", "NORDIC COFFEE ROASTERS AS", env,
                              lambda family, status, facts=None, reason=None: claims.append({"family": family, "status": status, "facts": facts or [], "reason": reason}))
        web = next(item for item in claims if item["family"] == "web_presence")
        self.assertEqual(web["status"], "available")
        self.assertEqual({fact["field"] for fact in web["facts"]}, {"website_url", "website_title"})
        self.assertEqual(env["operations"]["requests"], 2)

    def test_identity_in_bounded_legal_page_can_verify_brand_homepage(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        responses = [
            Response("https://example.no/robots.txt", b"User-agent: *\nAllow: /\n"),
            Response("https://example.no/", b'<title>Nordic Roast</title><a href="/legal">Legal information</a>'),
            Response("https://example.no/legal", b"<h1>Legal entity</h1><p>Org.nr 923 609 016</p>"),
        ]
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            self.agent.website("923609016", "https://example.no", "NORDIC COFFEE ROASTERS AS", env,
                               lambda *a, **k: claims.append(a))
        website = next(item for item in claims if item[0] == "web_presence")
        self.assertEqual(website[1], "available")
        self.assertEqual(len(website[2][0]["evidence_ids"]), 2)
        self.assertEqual(env["operations"]["requests"], 3)

    def test_formatted_org_number_is_recognized_exactly(self):
        self.assertTrue(sp.page_has_organisation_number("Org.nr. 923 609 016", "923609016"))
        self.assertTrue(sp.page_has_organisation_number("Org.nr: 923-609-016", "923609016"))
        self.assertFalse(sp.page_has_organisation_number("Org.nr. 923 609 017", "923609016"))
        self.assertFalse(sp.page_has_organisation_number("Product 30342669_923609016", "923609016"))

    def test_open_web_candidate_domain_must_match_distinctive_name(self):
        self.assertTrue(sp.domain_matches_legal_name("vvscompaniet.no", "VVS COMPANIET AS"))
        self.assertTrue(sp.domain_matches_legal_name("www.vvscompaniet.no", "VVS COMPANIET AS"))
        self.assertTrue(sp.domain_matches_legal_name("arena-trondheim.no", "ARENA TRONDHEIM AS"))
        self.assertFalse(sp.domain_matches_legal_name("forvalt.no", "ARV INVEST AS"))
        self.assertFalse(sp.domain_matches_legal_name("1850.no", "TU SAMDRIFT DA"))

    def test_private_host_is_rejected(self):
        self.assertFalse(sp.public_host("127.0.0.1"))
        self.assertFalse(sp.public_host("10.0.0.8"))
        self.assertTrue(sp.public_host("8.8.8.8"))

    def test_third_party_directory_is_not_published_as_company_website(self):
        env = {"operations": {"requests": 0}, "claims": [], "errors": [],
               "evidence": [], "source_snapshots": [], "changes": []}
        claims = []
        self.agent.website("919951486", "https://firmalisten.no/919951486",
                           "ARV INVEST AS", env,
                           lambda *args, **kwargs: claims.append((args, kwargs)))
        website = next(call for call in claims if call[0][0] == "web_presence")
        self.assertEqual(website[0][1], "ambiguous")
        self.assertIn("third-party business directory", website[1]["reason"])
        self.assertEqual(env["operations"]["requests"], 0)

    def test_kvantil_company_profile_is_not_published_as_company_website(self):
        env = {"operations": {"requests": 0}, "claims": [], "errors": [],
               "evidence": [], "source_snapshots": [], "changes": []}
        claims = []
        self.agent.website("997342992", "https://kvantil.no/company/997342992/concentration",
                           "CHOKOLADEFABRIKKEN UTBYGGINGSSELSKAP AS", env,
                           lambda *args, **kwargs: claims.append((args, kwargs)))
        website = next(call for call in claims if call[0][0] == "web_presence")
        self.assertEqual(website[0][1], "ambiguous")
        self.assertIn("third-party business directory", website[1]["reason"])
        self.assertEqual(env["operations"]["requests"], 0)

    def test_search_candidate_needs_same_host_legal_corroboration(self):
        class Headers:
            def __init__(self, content_type): self.content_type = content_type
            def get_content_type(self): return self.content_type
        class Response:
            def __init__(self, body, url, content_type): self.body,self.url,self.headers=body,url,Headers(content_type)
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def geturl(self): return self.url
            def read(self, size): return self.body
        responses = [
            Response(b"User-agent: *\\nAllow: /\\n", "https://market.example/robots.txt", "text/plain"),
            Response(b"<title>Example AS</title><h1>Example AS</h1><p>Product 30342669_923609016</p>",
                     "https://market.example/", "text/html"),
        ]
        env = {"operations": {"requests": 0}, "claims": [], "errors": [],
               "evidence": [], "source_snapshots": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            self.agent.website("923609016", "https://market.example/", "Example AS", env,
                               lambda *args, **kwargs: claims.append((args, kwargs)),
                               require_domain_name_match=True)
        website = next(call for call in claims if call[0][0] == "web_presence")
        self.assertEqual(website[0][1], "ambiguous")
        self.assertIn("same-host legal/contact evidence", website[1]["reason"])

    def test_brreg_listed_site_accepts_exact_legal_name_without_form_suffix(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        responses = [
            Response("https://arenatrondheim.no/robots.txt", b"User-agent: *\nAllow: /\n"),
            Response("https://arenatrondheim.no/", b"<title>Arena Trondheim</title><h1>Arena Trondheim</h1><p>Aktiviteter for alle.</p>"),
        ]
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            self.agent.website("992120193", "https://arenatrondheim.no/", "ARENA TRONDHEIM AS", env,
                               lambda family, status, facts=None, reason=None: claims.append({"family": family, "status": status, "facts": facts or [], "reason": reason}))
        website = next(item for item in claims if item["family"] == "web_presence")
        self.assertEqual(website["status"], "available")
        self.assertEqual(website["facts"][0]["value"], "https://arenatrondheim.no/")

    def test_registered_suffix_free_name_does_not_relax_search_candidates(self):
        self.assertTrue(sp.registered_name_base_matches_page(
            "ARENA TRONDHEIM AS", "Arena Trondheim hosts events.", "Arena Trondheim"))
        self.assertFalse(sp.legal_name_matches_page(
            "ARENA TRONDHEIM AS", "Arena Trondheim hosts events.", "Arena Trondheim"))

    def test_safe_website_requires_exact_legal_name_or_org_number(self):
        parser = sp.PageParser("https://example.no/")
        parser.feed("<title>Company</title><body>Example AS offers software.</body>")
        self.assertTrue(bool(__import__("re").search(r"(?<![\w])" + __import__("re").escape("Example AS") + r"(?![\w])", "Example AS offers software.", __import__("re").I)))
        self.assertFalse(bool(__import__("re").search(r"(?<![\w])" + __import__("re").escape("Example AS") + r"(?![\w])", "Example ASSETS", __import__("re").I)))

    def test_jsonld_job_entity_requires_exact_org_or_registered_name(self):
        parser = sp.PageParser("https://example.no/job")
        parser.feed('''<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer",
        "hiringOrganization":{"@type":"Organization","name":"Trading Brand","identifier":"923 609 016"}}</script>
        <script type="application/ld+json">{"@type":"JobPosting","title":"Other",
        "hiringOrganization":{"@type":"Organization","name":"Wrong AS","identifier":"987654321"}}</script>''')
        postings = [item for item in sp._jsonld_objects(parser) if "jobposting" in sp._jsonld_types(item)]
        self.assertEqual(len(postings), 2)
        self.assertTrue(sp._jsonld_exact_entity(postings[0], "923609016", "Example AS"))
        self.assertFalse(sp._jsonld_exact_entity(postings[1], "923609016", "Example AS"))


class RolePayloadCompatibilityTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.cwd() / "work"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.agent = sp.Agent(Path(self.tmp.name) / "state.sqlite3", website=False)

    def tearDown(self):
        self.agent.db.close()
        self.tmp.cleanup()

    def _extract_names(self, roles):
        payload = {"rollegrupper": [{"roller": roles}]}
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)):
            self.agent.roles("923609016", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        people = next(call[0] for call in claims if call[0][0] == "people")
        return [fact["value"]["name"] for fact in people[2]]

    def test_official_nested_brreg_name_shape_is_preserved(self):
        names = self._extract_names([{"type": {"beskrivelse": "Styreleder"}, "person": {"navn": {"fornavn": "Ola", "mellomnavn": "Per", "etternavn": "Nordmann"}}}])
        self.assertEqual(names, ["Ola Per Nordmann"])

    def test_flat_name_fields_are_supported_as_compatibility_shape(self):
        names = self._extract_names([{"type": {"beskrivelse": "Styreleder"}, "person": {"fornavn": "Kari", "mellomnavn": None, "etternavn": "Hansen"}}])
        self.assertEqual(names, ["Kari Hansen"])

    def test_malformed_role_entries_are_skipped_without_losing_valid_people(self):
        names = self._extract_names([None, {"person": "invalid"}, {"type": "invalid", "person": {"navn": {"fornavn": "Ola", "etternavn": "Nordmann"}}}])
        self.assertEqual(names, ["Ola Nordmann"])


class PublicActivityTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.cwd() / "work"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.agent = sp.Agent(Path(self.tmp.name) / "state.sqlite3", website=True)

    def tearDown(self):
        self.agent.db.close()
        self.tmp.cleanup()

    def test_dated_same_company_news_page_becomes_evidenced_activity(self):
        class Headers:
            def __init__(self, kind): self.kind = kind
            def get_content_type(self): return "text/plain" if self.kind == "robots" else "text/html"
        class Response:
            def __init__(self, body, kind): self.body, self.kind, self.headers = body, kind, Headers(kind)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://example.no/robots.txt" if self.kind == "robots" else "https://example.no/" if self.kind == "home" else "https://example.no/news/update"
            def read(self, limit): return self.body
        robots = b"User-agent: *\nAllow: /\n"
        home = b'<title>Example AS</title><h1>Example AS</h1><a href="/news/update">News</a>'
        article = b'<meta property="article:published_time" content="2026-10-15T09:00:00+02:00"><title>Example AS expands</title><h1>Example AS expands</h1><p>Example AS opened a new office.</p>'
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[Response(robots, "robots"), Response(home, "home"), Response(article, "article")]):
            self.agent.website("923609016", "https://example.no", "Example AS", env, lambda *args, **kwargs: claims.append((args, kwargs)))
        activity = next(call[0] for call in claims if call[0][0] == "public_activity")
        self.assertEqual(activity[1], "available")
        self.assertEqual(activity[2][0]["value"]["published_at"], "2026-10-15")
        self.assertEqual(len(activity[2][0]["evidence_ids"]), 1)

    def test_jsonld_article_needs_matching_publisher_and_uses_structured_date(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        html = b'''<title>Example AS</title><h1>Example AS</h1>
        <script type="application/ld+json">{"@type":"NewsArticle","headline":"New office opened",
        "datePublished":"2026-10-16T09:00:00Z","publisher":{"@type":"Organization","name":"Example AS","identifier":"923609016"}}</script>'''
        responses = [Response("https://example.no/robots.txt", b"User-agent: *\nAllow: /"),
                     Response("https://example.no/", html)]
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            self.agent.website("923609016", "https://example.no", "Example AS", env,
                               lambda *a, **k: claims.append(a))
        activity = next(call for call in claims if call[0] == "public_activity")
        self.assertEqual(activity[1], "available")
        self.assertEqual(activity[2][0]["value"], {"title": "New office opened", "published_at": "2026-10-16"})

    def test_activity_listing_follows_one_verified_article_detail(self):
        class Headers:
            def get_content_type(self): return "text/html"
        class Response:
            headers = Headers()
            def __init__(self, url, body): self.url, self.body = url, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, _limit): return self.body
        root = b'<title>Example AS</title><h1>Example AS</h1><a href="/news">News</a>'
        listing = b'<title>News</title><a href="/news/update">Read update</a>'
        article = b'''<title>Update</title><script type="application/ld+json">{"@type":"NewsArticle",
        "headline":"New office opened","datePublished":"2026-10-16T09:00:00Z",
        "publisher":{"@type":"Organization","name":"Example AS","identifier":"923609016"}}</script>'''
        responses = [Response("https://example.no/robots.txt", b"User-agent: *\nAllow: /"),
                     Response("https://example.no/", root), Response("https://example.no/news", listing),
                     Response("https://example.no/news/update", article)]
        env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
        claims = []
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=responses):
            self.agent.website("923609016", "https://example.no", "Example AS", env,
                               lambda *a, **k: claims.append(a))
        activity = next(call for call in claims if call[0] == "public_activity")
        self.assertEqual(activity[1], "available")
        self.assertEqual(activity[2][0]["value"]["title"], "New office opened")
        self.assertEqual(env["operations"]["requests"], 4)


if __name__ == "__main__":
    unittest.main()
