"""Independent regressions from QA_TEST_REPORT.md; fixtures do not contact the network."""
import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import claim_judge
import signalpost_agent as sp


class QARegressionTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.cwd() / "work"
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.agent = sp.Agent(Path(self.tmp.name) / "state.sqlite3", website=False)
        self.addCleanup(self.agent.db.close)

    def write(self, name, value):
        path = Path(self.tmp.name) / name
        path.write_text(value, encoding="utf-8")
        return path

    def test_registry_number_mismatch_fails_closed_before_evidence(self):
        payload = {"organisasjonsnummer": "999999999", "navn": "Other AS"}
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "failed")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["source_snapshots"], [])
        self.assertIn("mismatch", result["errors"][0]["type"])

    def test_invalid_numbers_do_not_make_source_calls(self):
        with patch.object(sp, "get_json", side_effect=AssertionError("invalid input reached network")) as getter:
            for value in ("", "12345678", "923609017", "٩٢٣٦٠٩٠١٦", "１２３４５６７８９"):
                with self.subTest(value=value):
                    result = self.agent.company(value)
                    self.assertEqual(result["identity_anchor"]["status"], "not_applicable")
        getter.assert_not_called()

    def test_registry_http_statuses_are_explicit_and_summary_stays_unverified(self):
        url = f"{sp.BRREG}/enheter/923609016"
        for code, expected in ((404, "not_available"), (403, "blocked"), (429, "blocked"), (500, "failed")):
            with self.subTest(status=code), patch.object(sp, "get_json", side_effect=__import__("urllib.error").error.HTTPError(url, code, "fixture", {}, None)):
                result = self.agent.company("923609016")
            self.assertEqual(result["identity_anchor"]["status"], expected)
            self.assertNotIn("is registered in Norway", result["summary"]["text"])

    def test_malformed_registry_root_returns_complete_failed_profile(self):
        with patch.object(sp, "get_json", return_value=([], b"[]", 200)):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "failed")
        self.assertEqual({c["family"] for c in result["claims"]}, set(sp.FAMILIES))

    def test_malformed_optional_registry_objects_do_not_abort_verified_identity(self):
        payload = {"organisasjonsnummer": "923609016", "navn": "Example AS",
                   "organisasjonsform": "unexpected", "naeringskode1": [],
                   "forretningsadresse": "unexpected"}
        with patch.object(sp, "get_json", side_effect=[
            (payload, json.dumps(payload).encode(), 200),
            ({"rollegrupper": []}, b'{"rollegrupper":[]}', 200),
            ({"regnskap": []}, b'{"regnskap":[]}', 200),
        ]):
            result = self.agent.company("923609016")
        self.assertEqual(result["identity_anchor"]["status"], "verified")
        self.assertFalse(any(f["field"] in {"organisation_form", "industry_code", "address"}
                             for c in result["claims"] for f in c["facts"]))

    def test_json_loader_rejects_unsupported_roots_and_containers(self):
        for name, content in (
            ("string.json", '"923609016"'),
            ("number.json", "42"),
            ("null.json", "null"),
            ("bool.json", "true"),
            ("string-list.json", '{"companies":"923609016"}'),
            ("unknown.json", '{"other":1}'),
        ):
            with self.subTest(name=name):
                path = self.write(name, content)
                with self.assertRaises(ValueError):
                    sp.load_orgs(path)

    def test_jsonl_malformed_record_is_not_silently_reinterpreted_as_a_company_number(self):
        path = self.write("bad.jsonl", '{"organisation_number":\n')
        with self.assertRaises(ValueError):
            sp.load_orgs(path)

    def test_jsonl_keeps_unicode_line_separator_inside_record(self):
        path = self.write("unicode.jsonl", json.dumps({"text": "alpha\u2028beta"}, ensure_ascii=False) + "\n")
        rows = sp.read_jsonl_records(path)
        self.assertEqual(rows[0]["text"], "alpha\u2028beta")

    def test_dashboard_keeps_unicode_line_separator_inside_json_string(self):
        path = self.write("unicode-profiles.jsonl", json.dumps({"text": "alpha\u2028beta"}, ensure_ascii=False) + "\n")
        dashboard = sp.make_dashboard(path)
        self.assertTrue(dashboard.exists())

    def test_page_parser_ignores_script_and_style_text(self):
        parser = sp.PageParser("https://example.test/")
        parser.feed('<title>Example AS</title><script>923609016</script><style>fake-company</style><p>Visible words</p>')
        text = " ".join(parser.text)
        self.assertNotIn("923609016", text)
        self.assertNotIn("fake-company", text)
        self.assertIn("Visible words", text)

    def test_page_parser_reads_description_independent_of_attribute_order_and_quotes(self):
        parser = sp.PageParser("https://example.test/")
        parser.feed('<meta content="We\'re hiring" name="description">')
        self.assertEqual(parser.meta_description, "We're hiring")

    def test_org_number_entity_match_requires_exact_digit_boundaries(self):
        response = _Response(b'<title>Neighbour AS</title><p>1923609016</p>')
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=[_Response(b"User-agent: *\nAllow: /"), response]):
            env = {"operations": {"requests": 0}, "errors": [], "source_snapshots": [], "evidence": [], "changes": []}
            claims = []
            self.agent.website("923609016", "https://example.test", "Right AS", env, lambda *a, **k: claims.append(a))
        web = next(c for c in claims if c[0] == "web_presence")
        self.assertEqual(web[1], "ambiguous")

    def test_invalid_registered_site_url_does_not_abort_company_profile(self):
        with patch.object(sp, "public_host", return_value=True):
            env = {"operations": {"requests": 0}, "errors": [], "source_snapshots": [], "evidence": [], "changes": []}
            claims = []
            self.agent.website("923609016", "https://example.test:bad", "Example AS", env, lambda *a, **k: claims.append(a))
        self.assertEqual({x[0] for x in claims}, {"web_presence", "jobs", "public_activity"})

    def test_unsafe_website_urls_do_not_attempt_network_access(self):
        for site in ("http://example.test", "https://user:pass@example.test", "https://example.test:444", "https://example.test:bad", "https://[::1"):
            with self.subTest(site=site), patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", side_effect=AssertionError("unsafe URL fetched")) as opener:
                env = {"operations": {"requests": 0}, "errors": [], "source_snapshots": [], "evidence": [], "changes": []}
                claims = []
                self.agent.website("923609016", site, "Example AS", env, lambda *a, **k: claims.append(a))
            self.assertEqual({x[0] for x in claims}, {"web_presence", "jobs", "public_activity"})
            opener.assert_not_called()

    def test_robots_disallow_stops_before_homepage_fetch(self):
        robots = _Response(b"User-agent: *\nDisallow: /\n", kind="robots")
        with patch.object(sp, "public_host", return_value=True), patch.object(sp, "open_safe", return_value=robots) as opener:
            env = {"operations": {"requests": 0}, "errors": [], "source_snapshots": [], "evidence": [], "changes": []}
            claims = []
            self.agent.website("923609016", "https://example.test", "Example AS", env, lambda *a, **k: claims.append(a))
        self.assertEqual({x[1] for x in claims}, {"blocked"})
        self.assertEqual(opener.call_count, 1)

    def test_redirect_handler_rejects_cross_domain_target(self):
        handler = sp.SameHostRedirect("example.test")
        request = __import__("urllib.request").request.Request("https://example.test/")
        for target in ("https://attacker.test/", "https://user:pass@example.test/secret"):
            with self.subTest(target=target), self.assertRaises(ValueError), patch.object(sp, "public_host", return_value=True):
                handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_pinned_https_connection_rejects_private_dns_answer_before_connect(self):
        private_answer = [(2, 1, 6, "", ("127.0.0.1", 443))]
        conn = sp.PinnedHTTPSConnection("company.example", timeout=1)
        with patch.object(sp.socket, "getaddrinfo", return_value=private_answer), patch.object(sp.socket, "socket") as create_socket:
            with self.assertRaisesRegex(OSError, "non-public address"):
                conn.connect()
        create_socket.assert_not_called()

    def test_cli_preserves_one_terminal_profile_per_invalid_input(self):
        source = self.write("invalid-batch.txt", "123\n923609017\n")
        output = Path(self.tmp.name) / "invalid-profiles.jsonl"
        state = Path(self.tmp.name) / "invalid-state.sqlite3"
        argv = ["signalpost_agent.py", "--input", str(source), "--output", str(output), "--state", str(state), "--no-website"]
        with patch.object(sys, "argv", argv), patch.object(sp, "get_json", side_effect=AssertionError("invalid input reached network")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sp.main(), 0)
        rows = sp.read_jsonl_records(output)
        report = json.loads(output.with_suffix(output.suffix + ".report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["identity_anchor"]["status"] == "not_applicable" for row in rows))
        self.assertEqual(report["input_count"], 2)
        self.assertEqual(report["output_count"], 2)
        self.assertTrue(report["all_inputs_have_terminal_result"])

    def test_nok_financial_fields_reject_non_nok_currency_and_nan(self):
        org = "923609016"
        for currency, amount in (("EUR", 123.0), ("NOK", float("nan")), ("NOK", float("inf"))):
            payload = {"regnskap": [{"virksomhet": {"organisasjonsnummer": org}, "valuta": currency,
                                      "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": amount}}}}]}
            with self.subTest(currency=currency, amount=amount), patch.object(sp, "get_json", return_value=(payload, json.dumps(payload, allow_nan=True).encode(), 200)):
                env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
                claims = []
                self.agent.financials(org, env, lambda *a, **k: claims.append(a))
            finance = next(c for c in claims if c[0] == "financials")
            self.assertFalse(any(f["field"] == "revenue_nok" for f in finance[2]))

    def test_malformed_financial_statement_does_not_hide_valid_statement(self):
        org = "923609016"
        good = {"virksomhet": {"organisasjonsnummer": org}, "valuta": "NOK",
                "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 500}}}}
        payload = {"regnskap": [{"virksomhet": "bad-shape"}, good]}
        with patch.object(sp, "get_json", return_value=(payload, json.dumps(payload).encode(), 200)):
            env = {"operations": {"requests": 0}, "source_snapshots": [], "evidence": [], "errors": [], "changes": []}
            claims = []
            self.agent.financials(org, env, lambda *a, **k: claims.append(a))
        finance = next(c for c in claims if c[0] == "financials")
        self.assertIn(("revenue_nok", 500), [(f["field"], f["value"]) for f in finance[2]])

    def test_registration_date_is_not_used_as_validity_date_for_unrelated_fields(self):
        data = {"organisasjonsnummer": "923609016", "navn": "Example AS", "organisasjonsform": {"kode": "AS"},
                "registreringsdatoEnhetsregisteret": "2000-01-01", "antallAnsatte": 10, "hjemmeside": "https://example.no"}
        with patch.object(sp, "get_json", side_effect=[(data, json.dumps(data).encode(), 200),
                                                         ({"rollegrupper": []}, b'{"rollegrupper":[]}', 200),
                                                         ({"regnskap": []}, b'{"regnskap":[]}', 200)]):
            result = self.agent.company("923609016")
        facts = next(c for c in result["claims"] if c["family"] == "company_details")["facts"]
        self.assertIsNone(next(f for f in facts if f["field"] == "employee_count").get("valid_at"))
        self.assertIsNone(next(f for f in facts if f["field"] == "website").get("valid_at"))

    def test_iso_date_helper_rejects_malformed_registry_date_values(self):
        self.assertEqual(sp._iso_date("2024-02-29"), "2024-02-29")
        self.assertIsNone(sp._iso_date("2024-02-30"))
        self.assertIsNone(sp._iso_date(20240229))

    def test_documented_brreg_company_statuses_and_dates_are_evidenced(self):
        data = {"organisasjonsnummer": "923609016", "navn": "Example AS", "konkurs": True,
                "konkursdato": "2024-01-04", "underAvvikling": True,
                "underAvviklingDato": "2024-02-05",
                "underTvangsavviklingEllerTvangsopplosning": True}
        with patch.object(sp, "get_json", side_effect=[
            (data, json.dumps(data).encode(), 200),
            ({"rollegrupper": []}, b'{"rollegrupper":[]}', 200),
            ({"regnskap": []}, b'{"regnskap":[]}', 200),
        ]):
            result = self.agent.company("923609016")
        facts = {f["field"]: f for c in result["claims"] if c["family"] == "company_details" for f in c["facts"]}
        self.assertTrue(facts["bankruptcy"]["value"])
        self.assertEqual(facts["bankruptcy_date"]["value"], "2024-01-04")
        self.assertEqual(facts["bankruptcy_date"]["valid_at"], "2024-01-04")
        self.assertTrue(facts["under_liquidation"]["value"])
        self.assertEqual(facts["under_liquidation_date"]["valid_at"], "2024-02-05")
        self.assertTrue(facts["under_forced_liquidation"]["value"])
        self.assertIn("marks it as bankrupt", result["summary"]["text"])

    def test_finish_repairs_missing_duplicate_and_invalid_claims_without_asserts(self):
        env = {"organisation_number": "923609016", "identity_anchor": {"status": "failed"},
               "claims": [{"family": "company_details", "status": "bogus", "facts": [{"x": 1}]},
                          {"family": "company_details", "status": "available", "facts": []}],
               "errors": [], "run": {}, "operations": {}}
        result = self.agent.finish(env, 0.0)
        claims = result["claims"]
        self.assertEqual(len(claims), len(sp.FAMILIES))
        self.assertEqual({c["family"] for c in claims}, set(sp.FAMILIES))
        self.assertEqual(next(c for c in claims if c["family"] == "company_details")["status"], "failed")
        self.assertTrue(any(e["type"] == "internal_error" for e in result["errors"]))

    def test_finish_drops_fact_when_source_snapshot_belongs_to_another_company(self):
        org = "923609016"
        snap = self.agent.snapshot(org, "registry", f"{sp.BRREG}/enheter/{org}", b"fixture", {"organisasjonsnummer": org})
        snap["organisation_number"] = "999999999"
        env = {"organisation_number": org, "identity_anchor": {"status": "verified"},
               "claims": [{"family": "company_details", "status": "available", "facts": [
                   {"field": "legal_name", "value": "Wrong AS", "evidence_ids": [snap["id"]]}]}],
               "source_snapshots": [snap], "evidence": [{"id": snap["id"], "source_id": snap["id"],
                   "claim_fields": ["legal_name"], "excerpt": "Wrong AS"}], "errors": [], "changes": [], "run": {}, "operations": {}}
        result = self.agent.finish(env, 0.0)
        claim = next(item for item in result["claims"] if item["family"] == "company_details")
        self.assertEqual(claim["facts"], [])
        self.assertEqual(claim["status"], "failed")
        self.assertTrue(any(error["type"] == "evidence_integrity" for error in result["errors"]))

    def test_finish_drops_fact_when_evidence_field_does_not_match(self):
        org = "923609016"
        snap = self.agent.snapshot(org, "registry", f"{sp.BRREG}/enheter/{org}", b"fixture2", {"organisasjonsnummer": org})
        env = {"organisation_number": org, "identity_anchor": {"status": "verified"},
               "claims": [{"family": "company_details", "status": "available", "facts": [
                   {"field": "legal_name", "value": "Example AS", "evidence_ids": [snap["id"]]}]}],
               "source_snapshots": [snap], "evidence": [{"id": snap["id"], "source_id": snap["id"],
                   "claim_fields": ["address"], "excerpt": "Example AS"}], "errors": [], "changes": [], "run": {}, "operations": {}}
        result = self.agent.finish(env, 0.0)
        self.assertEqual(next(item for item in result["claims"] if item["family"] == "company_details")["facts"], [])

    def test_open_safe_retries_transient_server_error_and_counts_extra_request(self):
        request = __import__("urllib.request").request.Request("https://example.no/")
        response = type("Response", (), {"status": 200})()
        opener = type("Opener", (), {"open": unittest.mock.Mock(side_effect=[
            __import__("urllib.error").error.HTTPError(request.full_url, 500, "server error", {}, None), response])})()
        callback = unittest.mock.Mock()
        with patch.object(sp.urllib.request, "build_opener", return_value=opener), patch.object(sp.time, "sleep"):
            self.assertIs(sp.open_safe(request, "example.no", 2, on_retry=callback), response)
        self.assertEqual(opener.open.call_count, 2)
        callback.assert_called_once_with()

    def test_open_safe_does_not_retry_not_found(self):
        request = __import__("urllib.request").request.Request("https://example.no/")
        opener = type("Opener", (), {"open": unittest.mock.Mock(side_effect=__import__("urllib.error").error.HTTPError(request.full_url, 404, "not found", {}, None))})()
        callback = unittest.mock.Mock()
        with patch.object(sp.urllib.request, "build_opener", return_value=opener), patch.object(sp.time, "sleep"):
            with self.assertRaises(__import__("urllib.error").error.HTTPError):
                sp.open_safe(request, "example.no", 2, on_retry=callback)
        self.assertEqual(opener.open.call_count, 1)
        callback.assert_not_called()

    def test_cli_refuses_to_overwrite_input_with_output(self):
        source = self.write("same.txt", "not-a-valid-company-number\n")
        state = Path(self.tmp.name) / "collision-state.sqlite3"
        original = source.read_text(encoding="utf-8")
        with patch.object(sys, "argv", ["signalpost_agent.py", "--input", str(source), "--output", str(source), "--state", str(state)]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                sp.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(source.read_text(encoding="utf-8"), original)
        self.assertFalse(state.exists())

    def test_optional_judge_rejects_conflicting_org_even_if_name_matches(self):
        candidate = {"field": "job", "value": "Engineer", "source_url": "https://example.no/jobs",
                     "quote": "The company is currently hiring an engineer for a full-time role.",
                     "as_of": "2026-10-01", "entity_org_number": "999999999", "entity_name": "Right AS"}
        self.assertEqual(claim_judge.WebSearchJudge._reject_reason(candidate, "923609016", "Right AS"), "entity_mismatch")

    def test_optional_judge_rejects_stale_candidate_dates(self):
        candidate = {"field": "job", "value": "Engineer", "source_url": "https://example.no/jobs",
                     "quote": "The company is currently hiring an engineer for a full-time role.",
                     "as_of": "2010-01-01", "entity_org_number": "923609016", "entity_name": "Right AS"}
        self.assertEqual(claim_judge.WebSearchJudge._reject_reason(candidate, "923609016", "Right AS"), "stale_or_invalid_date")

    def test_optional_judge_counts_only_actual_provider_calls(self):
        output = {"facts": [{"field": "job", "value": "Engineer", "source_url": "https://example.no/jobs",
                             "quote": "The company is currently hiring an engineer for a full-time role.",
                             "as_of": "2026-10-01", "entity_org_number": "923609016", "entity_name": "Right AS"}]}
        body = json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output)}]}]}).encode()
        judge = claim_judge.WebSearchJudge(api_key="fixture-key", max_calls=1)
        with patch.object(claim_judge.urllib.request, "urlopen", return_value=_ApiResponse(body)) as request:
            first = judge.discover("923609016", "Right AS")
            second = judge.discover("923609016", "Right AS")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(first["calls_made"], 1)
        self.assertEqual(first["facts"][0]["verification"], "candidate_only_web_search_citation_requires_human_or_source_fetch_check")
        self.assertEqual(second["calls_made"], 0)
        self.assertEqual(second["status"], "budget_exhausted")


class _Headers:
    def get_content_type(self):
        return "text/plain" if self.kind == "robots" else "text/html"


class _Response:
    def __init__(self, body, kind="html"):
        self.body, self.kind, self.headers = body, kind, _Headers()
        self.headers.kind = kind
        self.status = 200

    def __enter__(self): return self
    def __exit__(self, *args): return None
    def geturl(self): return "https://example.test/robots.txt" if self.kind == "robots" else "https://example.test/"
    def read(self, size=-1): return self.body if size < 0 else self.body[:size]


class _ApiResponse:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self, size=-1): return self.body if size < 0 else self.body[:size]


if __name__ == "__main__":
    unittest.main()
