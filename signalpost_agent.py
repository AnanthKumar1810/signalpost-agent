"""Evidence-first Signalpost runner. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import os
import re
import sqlite3
import socket
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

BRREG = "https://data.brreg.no/enhetsregisteret/api"
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
CONTACT = re.sub(r"[^A-Za-z0-9@.+_-]", "", os.environ.get("SIGNALPOST_CONTACT", "contact-not-configured"))[:120]
UA = f"SignalpostResearchAgent/0.2 (contact: {CONTACT})"
STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
FAMILIES = ("company_details", "people", "locations", "financials", "jobs", "public_activity", "web_presence")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_org(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    check = sum(int(d) * w for d, w in zip(value[:8], weights)) % 11
    digit = (11 - check) % 11
    return digit != 10 and digit == int(value[8])


def _find_statements(value: Any, org: str) -> list[dict[str, Any]]:
    """Find only annual-account records whose embedded organisation number matches."""
    found: list[dict[str, Any]] = []
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            business = node.get("virksomhet") or {}
            if business.get("organisasjonsnummer") == org and ("resultatregnskapResultat" in node or "eiendeler" in node):
                found.append(node)
            for child in node.values(): visit(child)
        elif isinstance(node, list):
            for child in node: visit(child)
    visit(value)
    return found


def public_host(host: str) -> bool:
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
        except OSError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def get_json(url: str, timeout: float = 12) -> tuple[Any, bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    host=urllib.parse.urlparse(url).hostname or ""
    with open_safe(req, host, timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response exceeds 2 MB safety limit")
        return json.loads(raw.decode("utf-8")), raw, response.status


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str): self.host = host
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed=urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != self.host or parsed.port not in (None,443) or not public_host(parsed.hostname or ""):
            raise ValueError("cross-domain or unsafe redirect rejected")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_safe(req: urllib.request.Request, host: str, timeout: float) -> Any:
    return urllib.request.build_opener(SameHostRedirect(host)).open(req,timeout=timeout)


class PageParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.text, self.title, self.h1 = base, [], [], []
        self.links: list[tuple[str, str]] = []
        self.in_title = False
        self.in_h1 = False
        self.jsonld: list[str] = []
        self.in_jsonld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "title": self.in_title = True
        if tag == "h1": self.in_h1 = True
        if tag == "script" and a.get("type") == "application/ld+json": self.in_jsonld = True
        if tag == "a" and a.get("href"):
            absolute = urllib.parse.urljoin(self.base, a["href"] or "")
            self.links.append((absolute, ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title": self.in_title = False
        if tag == "h1": self.in_h1 = False
        if tag == "script": self.in_jsonld = False

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean:
            self.text.append(clean)
            if self.in_title: self.title.append(clean)
            if self.in_h1: self.h1.append(clean)
            if self.in_jsonld: self.jsonld.append(data)


class Agent:
    def __init__(self, state_path: Path, website: bool = True, timeout: float = 12):
        self.db = sqlite3.connect(state_path)
        self.db.execute("create table if not exists snapshots(org text, family text, digest text, fetched_at text, payload text, primary key(org,family,digest))")
        self.db.execute("create table if not exists current_sources(org text, family text, digest text, fetched_at text, payload text, primary key(org,family))")
        self.db.commit()
        self.website_enabled, self.timeout = website, timeout

    def snapshot(self, org: str, family: str, source_url: str, raw: bytes, payload: Any) -> dict[str, Any]:
        digest = hashlib.sha256(raw).hexdigest()
        fetched = now()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        prior = self.db.execute("select digest,payload from current_sources where org=? and family=?", (org,family)).fetchone()
        changed = False
        changed_fields: list[str] = []
        if prior:
            try:
                old_obj, new_obj = json.loads(prior[1]), json.loads(encoded)
                if isinstance(old_obj, dict) and isinstance(new_obj, dict):
                    changed_fields = sorted(k for k in set(old_obj) | set(new_obj) if old_obj.get(k) != new_obj.get(k))
                else:
                    changed_fields = ["content"] if old_obj != new_obj else []
                changed = bool(changed_fields)
            except Exception:
                changed = prior[0] != digest
                changed_fields = ["content"] if changed else []
        self.db.execute("insert or ignore into snapshots values(?,?,?,?,?)", (org, family, digest, fetched, encoded))
        self.db.execute("insert into current_sources values(?,?,?,?,?) on conflict(org,family) do update set digest=excluded.digest,fetched_at=excluded.fetched_at,payload=excluded.payload", (org,family,digest,fetched,encoded))
        self.db.commit()
        return {"id": f"{family}:{digest[:16]}", "url": source_url, "retrieved_at": fetched, "sha256": digest, "license": "NLOD 2.0" if "brreg.no" in urllib.parse.urlparse(source_url).netloc else "source terms apply", "changed": changed, "changed_fields": changed_fields, "previous_sha256": prior[0] if prior else None}

    def company(self, org: str) -> dict[str, Any]:
        started = time.monotonic()
        env: dict[str, Any] = {"organisation_number": org, "identity_anchor": None, "claims": [], "evidence": [], "source_snapshots": [], "errors": [], "changes": [], "run": {"started_at": now()}, "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0.0}}
        def claim(family: str, status: str, facts: list[dict[str, Any]] | None = None, reason: str | None = None) -> None:
            env["claims"].append({"family": family, "status": status, "facts": facts or [], **({"reason": reason} if reason else {})})
        if not valid_org(org):
            env["identity_anchor"] = {"organisation_number": org, "status": "not_applicable"}
            claim("company_details", "not_applicable", reason="Invalid Norwegian organisation number")
            for family in FAMILIES[1:]: claim(family, "not_applicable", reason="Input is not a valid Norwegian organisation number")
            return self.finish(env, started)
        url = f"{BRREG}/enheter/{org}"
        env["operations"]["requests"] += 1
        try:
            data, raw, _ = get_json(url, self.timeout)
        except urllib.error.HTTPError as exc:
            env["errors"].append({"source": url, "type": "http_error", "status": exc.code, "message": str(exc)})
            status = "not_available" if exc.code in (404, 410) else "blocked" if exc.code in (401, 403, 429) else "failed"
            env["identity_anchor"] = {"organisation_number": org, "status": status}
            claim("company_details", status, reason=f"Registry returned HTTP {exc.code}")
            for family in FAMILIES:
                if family != "company_details": claim(family, "failed", reason="Identity registry lookup did not complete")
            return self.finish(env, started)
        except Exception as exc:
            env["errors"].append({"source": url, "type": "network_or_parse_error", "message": str(exc)})
            env["identity_anchor"] = {"organisation_number": org, "status": "failed"}
            claim("company_details", "failed", reason="Registry lookup failed")
            for family in FAMILIES:
                if family != "company_details": claim(family, "failed", reason="Identity registry lookup did not complete")
            return self.finish(env, started)
        snap = self.snapshot(org, "registry", url, raw, data)
        env["source_snapshots"].append(snap)
        if snap["changed"]: env["changes"].append({"family":"company_details","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
        name = data.get("navn")
        env["identity_anchor"] = {"organisation_number": org, "legal_name": name, "organisation_form": (data.get("organisasjonsform") or {}).get("kode"), "registry_url": url, "evidence_id": snap["id"], "status": "verified"}
        registry_fields = {"legal_name": name, "organisation_form": (data.get("organisasjonsform") or {}).get("kode"), "registered_at": data.get("registreringsdatoEnhetsregisteret"), "industry_code": (data.get("naeringskode1") or {}).get("kode"), "industry_description": (data.get("naeringskode1") or {}).get("beskrivelse"), "employee_count": data.get("antallAnsatte"), "website": data.get("hjemmeside"), "municipality": (data.get("forretningsadresse") or {}).get("kommune"), "postal_code": (data.get("forretningsadresse") or {}).get("postnummer"), "city": (data.get("forretningsadresse") or {}).get("poststed"), "address": data.get("forretningsadresse")}
        facts = [{"field": k, "value": v, "evidence_ids": [snap["id"]], "observed_at": snap["retrieved_at"], "valid_at": data.get("registreringsdatoEnhetsregisteret")} for k,v in registry_fields.items() if v not in (None, "", {})]
        env["evidence"].append({"id": snap["id"], "source_id": snap["id"], "excerpt": "; ".join(f"{f['field']}={f['value']}" for f in facts)[:1000], "claim_fields": [f["field"] for f in facts]})
        claim("company_details", "available" if facts else "not_available", facts)
        claim("locations", "available" if registry_fields["address"] else "not_available", [f for f in facts if f["field"] in {"address", "municipality", "postal_code", "city"}])
        self.roles(org, env, claim)
        self.financials(org, env, claim)
        site = registry_fields["website"]
        if self.website_enabled and site:
            self.website(org, site, name or "", env, claim)
        else:
            claim("web_presence", "not_available", reason="No registry-listed website" if not site else "Website crawling disabled")
            claim("jobs", "not_available", reason="No permitted company-owned careers page checked")
            claim("public_activity", "not_available", reason="No permitted company-owned activity source checked")
        return self.finish(env, started)

    def financials(self, org: str, env: dict[str, Any], claim: Any) -> None:
        """Read BRREG's public latest-year key figures as a separately evidenced source."""
        url = f"{ACCOUNTS}?{urllib.parse.urlencode({'orgNummer': org})}"
        env["operations"]["requests"] += 1
        try:
            data, raw, _ = get_json(url, self.timeout)
            snap = self.snapshot(org, "financials", url, raw, data)
            env["source_snapshots"].append(snap)
            if snap["changed"]:
                env["changes"].append({"family": "financials", "source_id": snap["id"], "previous_sha256": snap["previous_sha256"], "sha256": snap["sha256"], "changed_fields": snap["changed_fields"]})
            facts: list[dict[str, Any]] = []
            for statement in _find_statements(data, org):
                period = statement.get("regnskapsperiode") or {}
                result = statement.get("resultatregnskapResultat") or {}
                operating = result.get("driftsresultat") or {}
                revenue = operating.get("driftsinntekter") or {}
                equity_debt = statement.get("egenkapitalGjeld") or {}
                equity = equity_debt.get("egenkapital") or {}
                assets = statement.get("eiendeler") or {}
                for field, amount in {
                    "revenue_nok": revenue.get("sumDriftsinntekter"),
                    "operating_result_nok": operating.get("driftsresultat"),
                    "net_result_nok": result.get("aarsresultat"),
                    "total_assets_nok": assets.get("sumEiendeler"),
                    "equity_nok": equity.get("sumEgenkapital"),
                }.items():
                    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                        facts.append({"field": field, "value": amount, "currency": statement.get("valuta"), "evidence_ids": [snap["id"]], "observed_at": snap["retrieved_at"], "valid_at": period.get("tilDato"), "reporting_period": period})
            env["evidence"].append({"id": snap["id"], "source_id": snap["id"], "excerpt": "BRREG annual-account key figures matched to the requested organisation number", "claim_fields": sorted({fact["field"] for fact in facts})})
            claim("financials", "available" if facts else "not_available", facts, None if facts else "No public key figures were returned for this entity")
        except urllib.error.HTTPError as exc:
            env["errors"].append({"source": url, "type": "http_error", "status": exc.code, "message": str(exc)})
            status = "blocked" if exc.code in (401, 403, 429) else "not_available" if exc.code in (404, 410) else "failed"
            claim("financials", status, reason=f"Financial register returned HTTP {exc.code}")
        except Exception as exc:
            env["errors"].append({"source": url, "type": "fetch_error", "message": str(exc)})
            claim("financials", "failed", reason="Financial register lookup failed")

    def roles(self, org: str, env: dict[str, Any], claim: Any) -> None:
        url = f"{BRREG}/enheter/{org}/roller"
        env["operations"]["requests"] += 1
        try:
            data, raw, _ = get_json(url, self.timeout)
            snap = self.snapshot(org, "roles", url, raw, data)
            env["source_snapshots"].append(snap)
            if snap["changed"]: env["changes"].append({"family":"people","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
            roles = data.get("rollegrupper", []) if isinstance(data, dict) else []
            facts = []
            for group in roles:
                for item in group.get("roller", []):
                    person = item.get("person") or {}
                    value = {"role": item.get("type", {}).get("beskrivelse"), "name": " ".join(x for x in [person.get("navn"), person.get("etternavn")] if x)}
                    if value["name"]: facts.append({"field":"registered_role","value":value,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None})
            env["evidence"].append({"id":snap["id"],"source_id":snap["id"],"excerpt":"; ".join(f"{x['value'].get('role')}: {x['value'].get('name')}" for x in facts)[:1000],"claim_fields":["registered_role"]})
            claim("people", "available" if facts else "not_available", facts)
        except urllib.error.HTTPError as exc:
            env["errors"].append({"source":url,"type":"http_error","status":exc.code,"message":str(exc)})
            claim("people", "blocked" if exc.code in (401,403,429) else "failed", reason=f"Roles endpoint returned HTTP {exc.code}")
        except Exception as exc:
            env["errors"].append({"source":url,"type":"fetch_error","message":str(exc)})
            claim("people", "failed", reason="Roles endpoint failed")

    def website(self, org: str, site: str, legal_name: str, env: dict[str, Any], claim: Any) -> None:
        raw_site = site if site.startswith(("https://", "http://")) else "https://" + site
        parsed = urllib.parse.urlparse(raw_site)
        if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None,443) or parsed.username or parsed.password or not public_host(parsed.hostname):
            claim("web_presence", "ambiguous", reason="Registry website URL is not safe HTTPS")
            claim("jobs", "not_available", reason="Website URL was not crawled")
            claim("public_activity", "not_available", reason="Website URL was not crawled")
            return
        url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        env["operations"]["requests"] += 1
        try:
            robots_req = urllib.request.Request(robots_url, headers={"User-Agent":UA,"Accept":"text/plain"})
            try:
                with open_safe(robots_req, parsed.hostname, self.timeout) as robots_response:
                    if urllib.parse.urlparse(robots_response.geturl()).hostname != parsed.hostname:
                        raise ValueError("cross-domain robots redirect rejected")
                    robots_body = robots_response.read(100_001)
                    if len(robots_body)>100_000: raise ValueError("robots.txt exceeds 100 KB safety limit")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    robots_body = b""
                elif exc.code in (401,403,429):
                    claim("web_presence","blocked",reason=f"robots.txt returned HTTP {exc.code}")
                    claim("jobs","blocked",reason="robots.txt disallows company-site access")
                    claim("public_activity","blocked",reason="robots.txt disallows company-site access")
                    return
                else:
                    raise
            parser = RobotFileParser(robots_url)
            parser.parse(robots_body.decode("utf-8",errors="replace").splitlines())
            if not parser.can_fetch(UA,url):
                claim("web_presence","blocked",reason="robots.txt disallows the company homepage")
                claim("jobs","blocked",reason="robots.txt disallows company-site access")
                claim("public_activity","blocked",reason="robots.txt disallows company-site access")
                return
        except Exception as exc:
            env["errors"].append({"source":robots_url,"type":"robots_check_error","message":str(exc)})
            claim("web_presence","failed",reason="Could not verify the site's robots policy")
            claim("jobs","failed",reason="Could not verify the site's robots policy")
            claim("public_activity","failed",reason="Could not verify the site's robots policy")
            return
        env["operations"]["requests"] += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"text/html"})
            with open_safe(req, parsed.hostname, self.timeout) as resp:
                if urllib.parse.urlparse(resp.geturl()).hostname != parsed.hostname:
                    raise ValueError("cross-domain redirect rejected")
                body=resp.read(1_000_001)
                if len(body)>1_000_000: raise ValueError("page exceeds 1 MB safety limit")
                ctype=resp.headers.get_content_type()
                if ctype not in ("text/html", "application/xhtml+xml"): raise ValueError(f"unsupported content type {ctype}")
            text=body.decode("utf-8", errors="replace")
            p=PageParser(url); p.feed(text)
            title=" ".join(p.title).strip()
            page_text=" ".join(p.text)
            # A registry-declared domain is a verified relationship, but page claims need the legal name or exact org number.
            identity_heading = " ".join(p.title + p.h1)
            name_match=bool(legal_name and re.search(r"(?<![\w])" + re.escape(legal_name) + r"(?![\w])", identity_heading, re.I))
            org_match=org in page_text
            snap=self.snapshot(org,"website",url,body,{"title":title,"text":page_text[:8000]})
            env["source_snapshots"].append(snap)
            if snap["changed"]: env["changes"].append({"family":"web_presence","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
            ev={"id":snap["id"],"source_id":snap["id"],"excerpt":(title+" — "+page_text[:500])[:600],"claim_fields":["website_title","website_description"]}
            env["evidence"].append(ev)
            if name_match or org_match:
                facts=[{"field":"website_title","value":title,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None}]
                desc=re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',text,re.I)
                if desc: facts.append({"field":"website_description","value":html.unescape(desc.group(1)),"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None})
                claim("web_presence","available",facts)
                careers=[]
                for href,_ in p.links:
                    full=urllib.parse.urlparse(href)
                    if full.hostname==parsed.hostname and re.search(r"career|job|ledig|stilling|karriere",full.path,re.I):
                        careers.append(href)
                # A careers landing page is not evidence of an active vacancy.
                claim("jobs", "not_available", reason=("Careers link found, but no active vacancy was verified" if careers else "No active vacancy was verified"))
            else:
                claim("web_presence","ambiguous",reason="Website does not independently identify the exact legal entity")
                claim("jobs","not_available",reason="Website entity match failed; no jobs published")
            claim("public_activity","not_available",reason="No dated activity/article extraction implemented")
        except urllib.error.HTTPError as exc:
            st="blocked" if exc.code in (401,403,429) else "failed"
            env["errors"].append({"source":url,"type":"http_error","status":exc.code,"message":str(exc)})
            claim("web_presence",st,reason=f"Company site returned HTTP {exc.code}")
            claim("jobs",st,reason="Company site could not be checked")
        except Exception as exc:
            env["errors"].append({"source":url,"type":"fetch_error","message":str(exc)})
            claim("web_presence","failed",reason="Company website fetch or parse failed")
            claim("jobs","failed",reason="Company website could not be checked")

    def finish(self, env: dict[str, Any], started: float) -> dict[str, Any]:
        env["operations"]["runtime_ms"] = int((time.monotonic()-started)*1000)
        env["run"]["finished_at"] = now()
        env["run"]["id"] = str(uuid.uuid4())
        env["run"]["status"] = "complete"
        assert all(c["status"] in STATES for c in env["claims"])
        families = [c["family"] for c in env["claims"]]
        assert len(families) == len(set(families)), "each claim family must have one terminal status"
        assert set(families) == set(FAMILIES), "each company must have a terminal status for every claim family"
        env["summary"] = self._summary(env)
        return env

    @staticmethod
    def _summary(env: dict[str, Any]) -> dict[str, Any]:
        anchor = env.get("identity_anchor") or {}
        by_family = {claim["family"]: claim for claim in env["claims"]}
        details = by_family.get("company_details", {}).get("facts", [])
        fields = {fact.get("field"): fact for fact in details}
        name = anchor.get("legal_name") or env.get("organisation_number") or "Company"
        industry = fields.get("industry_description", {}).get("value")
        form = anchor.get("organisation_form")
        city = fields.get("city", {}).get("value")
        clauses = [f"{name} is registered in Norway"]
        if form: clauses[0] += f" as {form}"
        if industry: clauses.append(f"its registered industry is {industry}")
        if city: clauses.append(f"its registered business address is in {city}")
        evidence_ids = sorted({eid for fact in details for eid in fact.get("evidence_ids", [])})
        unknowns = [family for family in ("financials", "jobs", "public_activity") if by_family.get(family, {}).get("status") != "available"]
        return {"text": "; ".join(clauses) + ".", "evidence_ids": evidence_ids, "unknown_families": unknowns, "method": "deterministic evidence-grounded summary"}


def load_orgs(path: Path) -> list[str]:
    content=path.read_text(encoding="utf-8-sig")
    if path.suffix.lower()==".json":
        obj=json.loads(content)
        if isinstance(obj,dict):
            obj = [obj] if any(k in obj for k in ("organisation_number","orgnr")) else obj.get("companies",obj.get("organisations",obj.get("data",[])))
        return [str(x.get("organisation_number",x.get("orgnr",""))) if isinstance(x,dict) else str(x) for x in obj]
    rows=[]
    for line in content.splitlines():
        if not line.strip(): continue
        try:
            x=json.loads(line); rows.append(str(x.get("organisation_number",x.get("orgnr",""))) if isinstance(x,dict) else str(x))
        except json.JSONDecodeError: rows.append(line.strip())
    return rows


def make_dashboard(jsonl_path: Path) -> Path:
    rows=[json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    embedded=json.dumps(rows,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Signalpost company profiles</title>
<style>
:root{font:16px/1.45 system-ui,sans-serif;color:#17232b;background:#f4f7f7}body{margin:0}header{background:#123c44;color:white;padding:1.2rem max(1rem,calc((100vw - 1100px)/2))}main{max-width:1100px;margin:1rem auto;padding:0 1rem}.bar{display:flex;gap:.7rem;align-items:center;flex-wrap:wrap}.bar input{flex:1;min-width:14rem;padding:.8rem;border:1px solid #aab8ba;border-radius:.5rem;font:inherit}.layout{display:grid;grid-template-columns:minmax(16rem, .8fr) minmax(0,1.5fr);gap:1rem;margin-top:1rem}.list,.detail{background:white;border:1px solid #d8e0e1;border-radius:.6rem;padding:.8rem}.company{display:block;width:100%;text-align:left;background:white;border:0;border-bottom:1px solid #e5ebeb;padding:.75rem .5rem;font:inherit;cursor:pointer}.company:hover,.company:focus{background:#edf6f5}.muted{color:#58686c;font-size:.9rem}.pill{display:inline-block;border-radius:99px;padding:.1rem .5rem;background:#e5efed;font-size:.8rem}.claim{border-top:1px solid #e5ebeb;padding:.65rem 0}.claim h3{margin:.2rem 0;font-size:1rem}.fact{margin:.35rem 0}.source{font-size:.85rem}.empty{padding:1rem;color:#58686c}@media(max-width:700px){.layout{grid-template-columns:1fr}.list{max-height:35vh;overflow:auto}}
</style><header><h1>Signalpost company profiles</h1><div>Evidence-backed facts from the supplied company batch</div></header><main><div class="bar"><input id="q" type="search" placeholder="Search by company name or organisation number" aria-label="Search companies"><span id="count" class="muted"></span></div><div class="layout"><nav id="companies" class="list" aria-label="Companies"></nav><section id="profile" class="detail" aria-live="polite"><p class="empty">Choose a company to inspect its facts and sources.</p></section></div></main>
<script>const rows=JSON.parse('''+json.dumps(embedded)+''');const list=document.querySelector('#companies'),panel=document.querySelector('#profile'),query=document.querySelector('#q'),count=document.querySelector('#count');function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}function show(row){panel.replaceChildren();const id=row.identity_anchor||{};panel.append(el('h2',id.legal_name||row.organisation_number));panel.append(el('div','Org. no. '+row.organisation_number+' · '+(id.status||'unknown'),'muted'));if(row.summary?.text)panel.append(el('p',row.summary.text));for(const c of (row.claims||[])){const section=el('article',undefined,'claim');section.append(el('h3',c.family.replaceAll('_',' ')+' · '+c.status));if(c.reason)section.append(el('p',c.reason,'muted'));for(const f of (c.facts||[])){const value=typeof f.value==='object'?JSON.stringify(f.value):String(f.value);section.append(el('p',(f.field||'fact')+': '+value,'fact'));const ev=(f.evidence_ids||[]).map(x=>(row.source_snapshots||[]).find(s=>s.id===x)).filter(Boolean);for(const s of ev){const a=el('a',s.url,'source');a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';section.append(a,el('span',' · retrieved '+s.retrieved_at,'muted'))}}panel.append(section)}if(row.errors?.length){panel.append(el('h3','Retrieval errors'));for(const e of row.errors)panel.append(el('p',(e.type||'error')+': '+(e.message||''),'muted'))}}function render(){list.replaceChildren();const q=query.value.toLowerCase();const found=rows.filter(r=>((r.identity_anchor?.legal_name||'')+' '+r.organisation_number).toLowerCase().includes(q));count.textContent=found.length+' of '+rows.length+' companies';for(const r of found){const b=el('button',undefined,'company');b.type='button';b.append(el('strong',r.identity_anchor?.legal_name||r.organisation_number),el('div',r.organisation_number+' · '+(r.identity_anchor?.status||'unknown'),'muted'));b.onclick=()=>show(r);list.append(b)}}query.addEventListener('input',render);render();</script></html>'''
    destination=jsonl_path.with_suffix(jsonl_path.suffix+".html")
    destination.write_text(page,encoding="utf-8")
    return destination


def main() -> int:
    ap=argparse.ArgumentParser(description="Evidence-first Norwegian company research")
    ap.add_argument("--input",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    ap.add_argument("--state",type=Path,default=Path("signalpost.sqlite3"))
    ap.add_argument("--no-website",action="store_true")
    ap.add_argument("--timeout",type=float,default=12)
    args=ap.parse_args()
    orgs=load_orgs(args.input)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    agent=Agent(args.state,not args.no_website,args.timeout)
    n=0
    with args.output.open("w",encoding="utf-8") as out:
        for org in orgs:
            try: result=agent.company(org)
            except Exception as exc:
                failed_at=now()
                result={"organisation_number":org,"identity_anchor":{"organisation_number":org,"status":"failed"},"claims":[{"family":family,"status":"failed","facts":[],"reason":"Unexpected per-company processing error"} for family in FAMILIES],"evidence":[],"source_snapshots":[],"errors":[{"type":"internal_error","message":str(exc)}],"changes":[],"summary":{"text":"No verified company facts are available because processing failed.","evidence_ids":[],"unknown_families":list(FAMILIES),"method":"deterministic evidence-grounded summary"},"run":{"id":str(uuid.uuid4()),"started_at":failed_at,"finished_at":failed_at,"status":"complete"},"operations":{"requests":0,"runtime_ms":0,"third_party_cost_usd":0.0}}
            out.write(json.dumps(result,ensure_ascii=False)+"\n"); out.flush(); n+=1
    agent.db.close()
    dashboard=make_dashboard(args.output)
    states: dict[str,int] = {}
    families: dict[str,dict[str,int]] = {}
    requests = runtime = 0
    errors = 0
    costs = 0.0
    for line in args.output.read_text(encoding="utf-8").splitlines():
        row=json.loads(line)
        anchor=(row.get("identity_anchor") or {}).get("status","unknown")
        states[anchor]=states.get(anchor,0)+1
        requests += (row.get("operations") or {}).get("requests",0)
        runtime += (row.get("operations") or {}).get("runtime_ms",0)
        costs += (row.get("operations") or {}).get("third_party_cost_usd",0)
        errors += len(row.get("errors",[]))
        for c in row.get("claims",[]):
            bucket=families.setdefault(c.get("family","unknown"),{})
            status=c.get("status","unknown"); bucket[status]=bucket.get(status,0)+1
    report={"input_count":len(orgs),"output_count":n,"unique_input_count":len(set(orgs)),"all_inputs_have_terminal_result":n==len(orgs),"identity_status_counts":states,"family_status_counts":families,"requests":requests,"runtime_ms_sum":runtime,"errors":errors,"third_party_cost_usd":round(costs,6),"cost_basis":"actual third-party API charges; this implementation makes no paid API calls","output":str(args.output),"dashboard":str(dashboard)}
    args.output.with_suffix(args.output.suffix+".report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
