"""Evidence-first Signalpost runner. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import ipaddress
import json
import math
import os
import re
import sqlite3
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

BRREG = "https://data.brreg.no/enhetsregisteret/api"
# Query BRREG production. The PPE host is pre-production and has no service guarantee.
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
CONTACT = re.sub(r"[^A-Za-z0-9@.+_-]", "", os.environ.get("SIGNALPOST_CONTACT", "contact-not-configured"))[:120]
UA = f"SignalpostResearchAgent/0.2 (contact: {CONTACT})"
STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
FAMILIES = ("company_details", "people", "locations", "financials", "jobs", "public_activity", "web_presence")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def valid_org(value: str) -> bool:
    value = normalize_org(value)
    if not re.fullmatch(r"[0-9]{9}", value):
        return False
    weights = (3, 2, 7, 6, 5, 4, 3, 2)
    check = sum(int(d) * w for d, w in zip(value[:8], weights)) % 11
    digit = (11 - check) % 11
    return digit != 10 and digit == int(value[8])


def normalize_org(value: str) -> str:
    """Normalize common display spacing while leaving other invalid characters visible."""
    return re.sub(r"[\s\-]", "", str(value))


def _iso_date(value: Any) -> str | None:
    """Return a calendar date only when a source value is a valid ISO YYYY-MM-DD string."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _find_statements(value: Any, org: str) -> list[dict[str, Any]]:
    """Find only annual-account records whose embedded organisation number matches."""
    found: list[dict[str, Any]] = []
    seen_nodes: set[int] = set()
    seen_records: set[str] = set()
    def visit(node: Any) -> None:
        if not isinstance(node, (dict, list)) or id(node) in seen_nodes:
            return
        seen_nodes.add(id(node))
        if isinstance(node, dict):
            business = node.get("virksomhet")
            if isinstance(business, dict) and business.get("organisasjonsnummer") == org and ("resultatregnskapResultat" in node or "eiendeler" in node):
                record_key = json.dumps(node, ensure_ascii=False, sort_keys=True, allow_nan=True, default=str)
                if record_key not in seen_records:
                    found.append(node)
                    seen_records.add(record_key)
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


def get_json(url: str, timeout: float = 12, on_retry: Any = None) -> tuple[Any, bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    host=urllib.parse.urlparse(url).hostname or ""
    with open_safe(req, host, timeout, on_retry=on_retry) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response exceeds 2 MB safety limit")
        return json.loads(raw.decode("utf-8")), raw, response.status


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str): self.host = host
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed=urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != self.host or parsed.port not in (None,443) or parsed.username or parsed.password or not public_host(parsed.hostname or ""):
            raise ValueError("cross-domain or unsafe redirect rejected")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked public IP while keeping TLS verification on the hostname."""
    def connect(self) -> None:
        if self._tunnel_host:
            raise OSError("HTTPS proxy tunnels are disabled for safe company-site requests")
        port = self.port or 443
        addresses = socket.getaddrinfo(self.host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError("hostname did not resolve to an address")
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise OSError("hostname resolved to a non-public address")
        last_error: OSError | None = None
        for family, socktype, proto, _canonname, sockaddr in addresses:
            raw_socket = socket.socket(family, socktype, proto)
            raw_socket.settimeout(self.timeout)
            try:
                raw_socket.connect(sockaddr)
                self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                raw_socket.close()
        raise last_error or OSError("could not connect to a public address")


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(PinnedHTTPSConnection, req, context=self._context)


def open_safe(req: urllib.request.Request, host: str, timeout: float, on_retry: Any = None) -> Any:
    # Disable ambient HTTP(S)_PROXY settings so the checked address is the peer
    # that receives the connection; the custom HTTPS connection pins that peer.
    # These are idempotent GETs, so make one bounded retry for transient failures.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SameHostRedirect(host), PinnedHTTPSHandler())
    for attempt in range(2):
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in {500, 502, 503, 504}:
                raise
            exc.close()
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            if attempt:
                raise
        if on_retry:
            on_retry()
        time.sleep(0.25)
    raise RuntimeError("unreachable retry state")


class PageParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.text, self.title, self.h1 = base, [], [], []
        self.links: list[tuple[str, str]] = []
        self.in_title = False
        self.in_h1 = False
        self.active_link: int | None = None
        self.jsonld: list[str] = []
        self.in_jsonld = False
        self.meta_description: str | None = None
        self.published_dates: list[str] = []
        self._ignored_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self._ignored_tags:
            if tag in {"script", "style", "noscript", "template"}:
                self._ignored_tags.append(tag)
            return
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_tags.append(tag)
            self.in_jsonld = tag == "script" and (a.get("type") or "").lower() == "application/ld+json"
            return
        if tag == "meta" and (a.get("name") or "").strip().lower() == "description" and a.get("content") and self.meta_description is None:
            self.meta_description = html.unescape(a["content"] or "").strip()
        if tag == "meta":
            date_key = " ".join(str(a.get(key) or "") for key in ("name", "property", "itemprop")).lower()
            if any(key in date_key for key in ("published_time", "datepublished", "date_published", "publish_date")) and a.get("content"):
                self.published_dates.append(a["content"] or "")
        if tag == "time" and a.get("datetime"):
            self.published_dates.append(a["datetime"] or "")
        if tag == "title": self.in_title = True
        if tag == "h1": self.in_h1 = True
        if tag == "a" and a.get("href"):
            absolute = urllib.parse.urljoin(self.base, a["href"] or "")
            self.links.append((absolute, ""))
            self.active_link = len(self.links) - 1

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tags:
            if tag == self._ignored_tags[-1]:
                self._ignored_tags.pop()
                if tag == "script": self.in_jsonld = False
            return
        if tag == "title": self.in_title = False
        if tag == "h1": self.in_h1 = False
        if tag == "a": self.active_link = None

    def handle_data(self, data: str) -> None:
        if self._ignored_tags:
            if self.in_jsonld:
                self.jsonld.append(data)
            return
        clean = " ".join(data.split())
        if clean:
            self.text.append(clean)
            if self.in_title: self.title.append(clean)
            if self.in_h1: self.h1.append(clean)
            if self.in_jsonld: self.jsonld.append(data)
            if self.active_link is not None:
                href, caption = self.links[self.active_link]
                self.links[self.active_link] = (href, " ".join(part for part in (caption, clean) if part))


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
        return {"id": f"{family}:{digest[:16]}", "family": family, "organisation_number": org, "url": source_url, "retrieved_at": fetched, "sha256": digest, "license": "NLOD 2.0" if "brreg.no" in urllib.parse.urlparse(source_url).netloc else "source terms apply", "changed": changed, "changed_fields": changed_fields, "previous_sha256": prior[0] if prior else None}

    def company(self, org: str) -> dict[str, Any]:
        org = normalize_org(org)
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
            data, raw, _ = get_json(url, self.timeout, on_retry=lambda: env["operations"].__setitem__("requests", env["operations"]["requests"] + 1))
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
        if not isinstance(data, dict):
            env["errors"].append({"source": url, "type": "invalid_identity_shape", "message": "Registry response was not a JSON object"})
            env["identity_anchor"] = {"organisation_number": org, "status": "failed"}
            claim("company_details", "failed", reason="Registry response had an invalid shape")
            for family in FAMILIES:
                if family != "company_details": claim(family, "failed", reason="Identity registry lookup did not complete")
            return self.finish(env, started)
        returned_org = data.get("organisasjonsnummer")
        if not isinstance(returned_org, str) or normalize_org(returned_org) != org:
            env["errors"].append({"source": url, "type": "identity_mismatch", "message": "Registry response organisation number did not match the requested number"})
            env["identity_anchor"] = {"organisation_number": org, "status": "failed"}
            claim("company_details", "failed", reason="Registry entity could not be matched to the requested organisation number")
            for family in FAMILIES:
                if family != "company_details": claim(family, "failed", reason="Identity registry lookup did not complete")
            return self.finish(env, started)
        snap = self.snapshot(org, "registry", url, raw, data)
        env["source_snapshots"].append(snap)
        if snap["changed"]: env["changes"].append({"family":"company_details","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
        name = data.get("navn")
        organisation_form = data.get("organisasjonsform")
        organisation_form = organisation_form if isinstance(organisation_form, dict) else {}
        industry = data.get("naeringskode1")
        industry = industry if isinstance(industry, dict) else {}
        address = data.get("forretningsadresse")
        address = address if isinstance(address, dict) else {}
        env["identity_anchor"] = {"organisation_number": org, "legal_name": name, "organisation_form": organisation_form.get("kode"), "registry_url": url, "evidence_id": snap["id"], "status": "verified"}
        registry_fields = {"legal_name": name, "organisation_form": organisation_form.get("kode"), "registered_at": data.get("registreringsdatoEnhetsregisteret"), "industry_code": industry.get("kode"), "industry_description": industry.get("beskrivelse"), "employee_count": data.get("antallAnsatte"), "website": data.get("hjemmeside"), "municipality": address.get("kommune"), "postal_code": address.get("postnummer"), "city": address.get("poststed"), "address": address or None}
        # These status/date fields are documented in BRREG's Enhetsregisteret v2 entity response.
        # Emit positive statuses only; absence or false is not a noteworthy event.
        status_fields = {
            "bankruptcy": ("konkurs", "konkursdato"),
            "under_liquidation": ("underAvvikling", "underAvviklingDato"),
            "under_forced_liquidation": ("underTvangsavviklingEllerTvangsopplosning", None),
        }
        status_date_values: dict[str, str] = {}
        for output_field, (flag_key, date_key) in status_fields.items():
            if data.get(flag_key) is True:
                registry_fields[output_field] = True
                if date_key:
                    status_date = _iso_date(data.get(date_key))
                    if status_date:
                        date_field = f"{output_field}_date"
                        registry_fields[date_field] = status_date
                        status_date_values[date_field] = status_date
        facts = [{"field": k, "value": v, "evidence_ids": [snap["id"]], "observed_at": snap["retrieved_at"], "valid_at": (_iso_date(data.get("registreringsdatoEnhetsregisteret")) if k == "registered_at" else status_date_values.get(k))} for k,v in registry_fields.items() if v not in (None, "", {})]
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
        url = f"{ACCOUNTS}/{org}"
        env["operations"]["requests"] += 1
        try:
            data, raw, _ = get_json(url, self.timeout, on_retry=lambda: env["operations"].__setitem__("requests", env["operations"]["requests"] + 1))
            snap = self.snapshot(org, "financials", url, raw, data)
            env["source_snapshots"].append(snap)
            if snap["changed"]:
                env["changes"].append({"family": "financials", "source_id": snap["id"], "previous_sha256": snap["previous_sha256"], "sha256": snap["sha256"], "changed_fields": snap["changed_fields"]})
            facts: list[dict[str, Any]] = []
            for statement in _find_statements(data, org):
                if statement.get("valuta") != "NOK":
                    continue
                period = statement.get("regnskapsperiode") or {}
                result = statement.get("resultatregnskapResultat") or {}
                if not isinstance(period, dict) or not isinstance(result, dict):
                    continue
                operating = result.get("driftsresultat") or {}
                if not isinstance(operating, dict):
                    continue
                revenue = operating.get("driftsinntekter") or {}
                if not isinstance(revenue, dict):
                    revenue = {}
                equity_debt = statement.get("egenkapitalGjeld") or {}
                if not isinstance(equity_debt, dict):
                    equity_debt = {}
                equity = equity_debt.get("egenkapital") or {}
                if not isinstance(equity, dict):
                    equity = {}
                assets = statement.get("eiendeler") or {}
                if not isinstance(assets, dict):
                    assets = {}
                for field, amount in {
                    "revenue_nok": revenue.get("sumDriftsinntekter"),
                    "operating_result_nok": operating.get("driftsresultat"),
                    "net_result_nok": result.get("aarsresultat"),
                    "total_assets_nok": assets.get("sumEiendeler"),
                    "equity_nok": equity.get("sumEgenkapital"),
                }.items():
                    if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount):
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
            data, raw, _ = get_json(url, self.timeout, on_retry=lambda: env["operations"].__setitem__("requests", env["operations"]["requests"] + 1))
            snap = self.snapshot(org, "roles", url, raw, data)
            env["source_snapshots"].append(snap)
            if snap["changed"]: env["changes"].append({"family":"people","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
            roles = data.get("rollegrupper", []) if isinstance(data, dict) else []
            facts = []
            if isinstance(roles, list):
                for group in roles:
                    if not isinstance(group, dict):
                        continue
                    group_roles = group.get("roller", [])
                    if not isinstance(group_roles, list):
                        continue
                    for item in group_roles:
                        if not isinstance(item, dict):
                            continue
                        person = item.get("person")
                        if not isinstance(person, dict):
                            continue
                        # BRREG documents names under person.navn. Accept the
                        # equivalent flat fields too for older/alternate payloads.
                        name_data = person.get("navn")
                        name_fields = name_data if isinstance(name_data, dict) else person
                        parts = [name_fields.get(key) for key in ("fornavn", "mellomnavn", "etternavn")]
                        if not any(isinstance(part, str) and part.strip() for part in parts):
                            # Preserve compatibility with payloads that return
                            # person.navn as a single display-name string.
                            parts = [name_data, person.get("etternavn")]
                        name = " ".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())
                        value = {"role": (item.get("type") or {}).get("beskrivelse") if isinstance(item.get("type"), dict) else None, "name": name}
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
        count_retry = lambda: env["operations"].__setitem__("requests", env["operations"]["requests"] + 1)
        try:
            raw_site = site if site.startswith(("https://", "http://")) else "https://" + site
            parsed = urllib.parse.urlparse(raw_site)
            safe_url = parsed.scheme == "https" and bool(parsed.hostname) and parsed.port in (None,443) and not parsed.username and not parsed.password and public_host(parsed.hostname or "")
        except (TypeError, ValueError) as exc:
            env["errors"].append({"source": str(site), "type": "invalid_website_url", "message": str(exc)})
            claim("web_presence", "failed", reason="Registry website URL could not be parsed safely")
            claim("jobs", "failed", reason="Website URL could not be parsed safely")
            claim("public_activity", "failed", reason="Website URL could not be parsed safely")
            return
        if not safe_url:
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
                with open_safe(robots_req, parsed.hostname, self.timeout, on_retry=count_retry) as robots_response:
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
            with open_safe(req, parsed.hostname, self.timeout, on_retry=count_retry) as resp:
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
            org_match=bool(re.search(r"(?<![0-9])" + re.escape(org) + r"(?![0-9])", page_text))
            snap=self.snapshot(org,"website",url,body,{"title":title,"text":page_text[:8000]})
            env["source_snapshots"].append(snap)
            if snap["changed"]: env["changes"].append({"family":"web_presence","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
            ev={"id":snap["id"],"source_id":snap["id"],"excerpt":(title+" — "+page_text[:500])[:600],"claim_fields":["website_title","website_description"]}
            env["evidence"].append(ev)
            if name_match or org_match:
                facts=[{"field":"website_title","value":title,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None}]
                if p.meta_description: facts.append({"field":"website_description","value":p.meta_description,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None})
                claim("web_presence","available",facts)
                job_pages=[]
                for href,caption in p.links:
                    full=urllib.parse.urlparse(href)
                    if full.hostname==parsed.hostname and full.scheme=="https" and re.search(r"vacan|job|position|stilling|ledig",full.path+" "+caption,re.I):
                        job_pages.append(urllib.parse.urlunparse((full.scheme,full.netloc,full.path,"","","")))
                job_facts=[]
                for job_url in list(dict.fromkeys(job_pages))[:2]:
                    if not parser.can_fetch(UA,job_url):
                        continue
                    env["operations"]["requests"] += 1
                    job_req=urllib.request.Request(job_url,headers={"User-Agent":UA,"Accept":"text/html"})
                    with open_safe(job_req,parsed.hostname,self.timeout,on_retry=count_retry) as job_response:
                        if urllib.parse.urlparse(job_response.geturl()).hostname!=parsed.hostname:
                            raise ValueError("cross-domain job-page redirect rejected")
                        job_body=job_response.read(500_001)
                        if len(job_body)>500_000: raise ValueError("job page exceeds 500 KB safety limit")
                        if job_response.headers.get_content_type() not in ("text/html","application/xhtml+xml"):
                            continue
                    job_text=job_body.decode("utf-8",errors="replace")
                    job_parser=PageParser(job_url); job_parser.feed(job_text)
                    job_title=" ".join(job_parser.h1).strip() or " ".join(job_parser.title).strip()
                    job_words=" ".join(job_parser.text)
                    accepting=bool(re.search(r"apply(?: now)?|submit (?:your )?application|søk(?: nå| på stillingen)?",job_words,re.I))
                    closed=bool(re.search(r"position (?:is )?closed|applications? (?:are )?closed|no longer accepting|stillingen er besatt|søknadsfristen er utløpt",job_words,re.I))
                    if job_title and accepting and not closed:
                        job_snap=self.snapshot(org,"jobs",job_url,job_body,{"title":job_title,"text":job_words[:3000]})
                        env["source_snapshots"].append(job_snap)
                        if job_snap["changed"]: env["changes"].append({"family":"jobs","source_id":job_snap["id"],"previous_sha256":job_snap["previous_sha256"],"sha256":job_snap["sha256"],"changed_fields":job_snap["changed_fields"]})
                        env["evidence"].append({"id":job_snap["id"],"source_id":job_snap["id"],"excerpt":(job_title+" — "+job_words[:500])[:600],"claim_fields":["active_job_title"]})
                        job_facts.append({"field":"active_job_title","value":job_title,"evidence_ids":[job_snap["id"]],"observed_at":job_snap["retrieved_at"],"valid_at":None,"source_url":job_url})
                claim("jobs","available" if job_facts else "not_available",job_facts,None if job_facts else "No current vacancy with an application action was verified")
                activity_pages = [url]
                for href, caption in p.links:
                    full = urllib.parse.urlparse(href)
                    if full.hostname == parsed.hostname and full.scheme == "https" and re.search(r"news|press|blog|article|aktuelt|nyheter|nyhet", full.path + " " + caption, re.I):
                        activity_pages.append(urllib.parse.urlunparse((full.scheme, full.netloc, full.path, "", "", "")))
                activity_facts = []
                for activity_url in list(dict.fromkeys(activity_pages))[:3]:
                    if activity_url != url and not parser.can_fetch(UA, activity_url):
                        continue
                    if activity_url == url:
                        activity_body, activity_parser = body, p
                    else:
                        env["operations"]["requests"] += 1
                        activity_req = urllib.request.Request(activity_url, headers={"User-Agent": UA, "Accept": "text/html"})
                        with open_safe(activity_req, parsed.hostname, self.timeout, on_retry=count_retry) as activity_response:
                            if urllib.parse.urlparse(activity_response.geturl()).hostname != parsed.hostname:
                                raise ValueError("cross-domain activity redirect rejected")
                            activity_body = activity_response.read(500_001)
                            if len(activity_body) > 500_000:
                                raise ValueError("activity page exceeds 500 KB safety limit")
                            if activity_response.headers.get_content_type() not in ("text/html", "application/xhtml+xml"):
                                continue
                        activity_parser = PageParser(activity_url)
                        activity_parser.feed(activity_body.decode("utf-8", errors="replace"))
                    activity_heading = " ".join(activity_parser.title + activity_parser.h1)
                    activity_text = " ".join(activity_parser.text)
                    exact_name = bool(legal_name and re.search(r"(?<![\w])" + re.escape(legal_name) + r"(?![\w])", activity_heading + " " + activity_text, re.I))
                    exact_org = bool(re.search(r"(?<![0-9])" + re.escape(org) + r"(?![0-9])", activity_text))
                    if not (exact_name or exact_org):
                        continue
                    published = next((value[:10] for value in activity_parser.published_dates if _iso_date(value[:10])), None)
                    if not published:
                        continue
                    activity_title = activity_heading.strip()
                    if not activity_title:
                        continue
                    activity_snap = self.snapshot(org, "public_activity", activity_url, activity_body, {"title": activity_title, "published_at": published, "text": activity_text[:3000]})
                    env["source_snapshots"].append(activity_snap)
                    if activity_snap["changed"]:
                        env["changes"].append({"family": "public_activity", "source_id": activity_snap["id"], "previous_sha256": activity_snap["previous_sha256"], "sha256": activity_snap["sha256"], "changed_fields": activity_snap["changed_fields"]})
                    env["evidence"].append({"id": activity_snap["id"], "source_id": activity_snap["id"], "excerpt": f"{published} — {activity_title}: {activity_text[:400]}"[:600], "claim_fields": ["public_activity"]})
                    activity_facts.append({"field": "company_activity", "value": {"title": activity_title, "published_at": published}, "evidence_ids": [activity_snap["id"]], "observed_at": activity_snap["retrieved_at"], "valid_at": published, "source_url": activity_url})
                claim("public_activity", "available" if activity_facts else "not_available", activity_facts, None if activity_facts else "No dated, entity-matched company news or activity page was verified")
            else:
                claim("web_presence","ambiguous",reason="Website does not independently identify the exact legal entity")
                claim("jobs","not_available",reason="Website entity match failed; no jobs published")
                claim("public_activity","not_available",reason="Website entity match failed; no activity published")
        except urllib.error.HTTPError as exc:
            st="not_available" if exc.code in (404,410) else "blocked" if exc.code in (401,403,429) else "failed"
            env["errors"].append({"source":url,"type":"http_error","status":exc.code,"message":str(exc)})
            claim("web_presence",st,reason=f"Company site returned HTTP {exc.code}")
            claim("jobs",st,reason="Company site could not be checked")
            claim("public_activity",st,reason="Company site could not be checked")
        except Exception as exc:
            env["errors"].append({"source":url,"type":"fetch_error","message":str(exc)})
            claim("web_presence","failed",reason="Company website fetch or parse failed")
            claim("jobs","failed",reason="Company website could not be checked")
            claim("public_activity","failed",reason="Company website could not be checked")

    def finish(self, env: dict[str, Any], started: float) -> dict[str, Any]:
        env["operations"]["runtime_ms"] = int((time.monotonic()-started)*1000)
        env["run"]["finished_at"] = now()
        env["run"]["id"] = str(uuid.uuid4())
        env["run"]["status"] = "complete"
        snapshots = {item.get("id"): item for item in env.get("source_snapshots", []) if isinstance(item, dict) and item.get("id")}
        evidence = {item.get("id"): item for item in env.get("evidence", []) if isinstance(item, dict) and item.get("id")}
        snapshot_families = {"company_details": {"registry"}, "people": {"roles"}, "locations": {"registry"},
                             "financials": {"financials"}, "web_presence": {"website"}, "jobs": {"jobs"}, "public_activity": {"public_activity"}}
        claims: list[dict[str, Any]] = []
        present: set[str] = set()
        for item in env.get("claims", []):
            if not isinstance(item, dict):
                env["errors"].append({"type": "internal_error", "message": "Malformed claim was dropped"})
                continue
            family = item.get("family")
            if family not in FAMILIES or family in present:
                env["errors"].append({"type": "internal_error", "message": f"Unknown or duplicate claim family {family!r} was dropped"})
                continue
            present.add(family)
            if item.get("status") not in STATES or not isinstance(item.get("facts", []), list):
                env["errors"].append({"type": "internal_error", "message": f"Invalid claim for {family}; marked failed"})
                item = {**item, "status": "failed", "facts": []}
            facts = []
            if item.get("facts") and (env.get("identity_anchor") or {}).get("status") != "verified":
                env["errors"].append({"type": "evidence_integrity", "message": f"Facts for {family} dropped because the company identity is not verified"})
            else:
                for fact in item.get("facts", []):
                    evidence_ids = fact.get("evidence_ids") if isinstance(fact, dict) else None
                    source_family = snapshot_families.get(family, set())
                    good_ids = isinstance(evidence_ids, list) and bool(evidence_ids)
                    if good_ids:
                        for evidence_id in evidence_ids:
                            snap = snapshots.get(evidence_id)
                            ev = evidence.get(evidence_id)
                            if (not snap or not ev or snap.get("organisation_number") != env.get("organisation_number")
                                    or snap.get("family") not in source_family or ev.get("source_id") != evidence_id):
                                good_ids = False
                                break
                            field = fact.get("field")
                            allowed_fields = {field, "public_activity" if field == "company_activity" else field}
                            if not allowed_fields.intersection(ev.get("claim_fields", [])):
                                good_ids = False
                                break
                            if fact.get("source_url") and fact["source_url"] != snap.get("url"):
                                good_ids = False
                                break
                    try:
                        json.dumps(fact.get("value"), allow_nan=False)
                        good_value = fact.get("value") not in (None, "")
                    except (TypeError, ValueError):
                        good_value = False
                    if good_ids and good_value:
                        facts.append(fact)
                    else:
                        env["errors"].append({"type": "evidence_integrity", "message": f"Unsupported or mismatched fact dropped from {family}"})
                item = {**item, "facts": facts}
                if item.get("status") == "available" and not facts:
                    item["status"] = "failed"
                    item["reason"] = "All returned facts failed the evidence integrity check"
            claims.append(item)
        env["claims"] = claims
        for family in FAMILIES:
            if family not in present:
                env["claims"].append({"family": family, "status": "failed", "facts": [], "reason": "No terminal result was produced for this information family"})
                env["errors"].append({"type": "internal_error", "message": f"Missing terminal claim for {family}; emitted a failed status"})
        env["summary"] = self._summary(env)
        return env

    @staticmethod
    def _summary(env: dict[str, Any]) -> dict[str, Any]:
        anchor = env.get("identity_anchor") or {}
        if anchor.get("status") != "verified":
            return {"text": "Company identity could not be verified; no company-specific facts are available.", "evidence_ids": [], "unknown_families": list(FAMILIES), "method": "deterministic evidence-grounded summary"}
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
        if fields.get("bankruptcy", {}).get("value") is True: clauses.append("the registry marks it as bankrupt")
        if fields.get("under_liquidation", {}).get("value") is True: clauses.append("the registry marks it as under liquidation")
        if fields.get("under_forced_liquidation", {}).get("value") is True: clauses.append("the registry marks it as under forced liquidation or dissolution")
        evidence_ids = sorted({eid for fact in details for eid in fact.get("evidence_ids", [])})
        unknowns = [family for family in ("financials", "jobs", "public_activity") if by_family.get(family, {}).get("status") != "available"]
        return {"text": "; ".join(clauses) + ".", "evidence_ids": evidence_ids, "unknown_families": unknowns, "method": "deterministic evidence-grounded summary"}


def load_orgs(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8-sig")

    def record_value(value: Any, location: str) -> str:
        if isinstance(value, str):
            return normalize_org(value)
        if isinstance(value, dict):
            for key in ("organisation_number", "orgnr"):
                if key in value:
                    if not isinstance(value[key], str):
                        raise ValueError(f"{location}: {key} must be a string")
                    return normalize_org(value[key])
            raise ValueError(f"{location}: expected organisation_number or orgnr")
        raise ValueError(f"{location}: expected a string or company object, got {type(value).__name__}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            obj = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON input: {exc}") from exc
        if isinstance(obj, dict):
            if any(key in obj for key in ("organisation_number", "orgnr")):
                obj = [obj]
            else:
                for key in ("companies", "organisations", "data"):
                    if key in obj:
                        obj = obj[key]
                        break
                else:
                    raise ValueError("JSON object must contain a company record or companies/organisations/data array")
        if not isinstance(obj, list):
            raise ValueError("JSON batch root must be an array or supported object")
        return [record_value(item, f"JSON record {index + 1}") for index, item in enumerate(obj)]

    if suffix == ".jsonl":
        rows = []
        for line_number, line in enumerate(content.split("\n"), 1):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL record on line {line_number}: {exc.msg}") from exc
            rows.append(record_value(value, f"JSONL line {line_number}"))
        return rows

    return [normalize_org(line.strip()) for line in content.splitlines() if line.strip()]


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Read JSONL using LF record boundaries; Unicode separators are valid string data."""
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL record on line {line_number}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number}: profile record must be a JSON object")
        rows.append(value)
    return rows


def make_dashboard(jsonl_path: Path) -> Path:
    rows=read_jsonl_records(jsonl_path)
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
    ap.add_argument("--workers",type=int,default=4,help="Concurrent company lookups (default: 4)")
    ap.add_argument("--web-search-judge",action="store_true",help="Opt in to one OpenAI web-search call per valid company; results are unverified candidates, never facts")
    args=ap.parse_args()
    if args.workers < 1 or args.workers > 16:
        ap.error("--workers must be between 1 and 16")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        ap.error("--timeout must be a positive finite number")
    resolved_paths = {"input": args.input.resolve(), "output": args.output.resolve(), "state": args.state.resolve()}
    if resolved_paths["input"] == resolved_paths["output"]:
        ap.error("--input and --output must be different files")
    if resolved_paths["state"] in (resolved_paths["input"], resolved_paths["output"]):
        ap.error("--state must be a different file from --input and --output")
    try:
        orgs=load_orgs(args.input)
    except (OSError, ValueError) as exc:
        ap.error(f"could not read input batch: {exc}")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    judge = None
    if args.web_search_judge:
        try:
            from claim_judge import WebSearchJudge
            judge = WebSearchJudge(max_calls=1)
        except Exception as exc:
            ap.error(f"could not enable web-search judge: {exc}")
    agent=Agent(args.state,not args.no_website,args.timeout)
    n=0
    started_batch=time.monotonic()
    def process(org: str) -> dict[str, Any]:
        worker_agent=Agent(args.state,not args.no_website,args.timeout)
        try:
            result=worker_agent.company(org)
            if judge and result.get("identity_anchor", {}).get("status") == "verified":
                candidates = judge.discover(org,result["identity_anchor"].get("legal_name") or "")
                candidates = judge.verify_citations(candidates, org, result["identity_anchor"].get("legal_name") or "")
                result["web_search_candidates"] = candidates
                result["operations"]["requests"] += candidates.get("calls_made", 0) + candidates.get("verification_requests", 0)
                if candidates.get("calls_made", 0):
                    result["operations"]["third_party_cost_usd"] = None
                    result["operations"]["third_party_cost_note"] = "Provider cost was not measured; check the OpenAI usage dashboard."
            return result
        except Exception as exc:
            failed_at=now()
            return {"organisation_number":org,"identity_anchor":{"organisation_number":org,"status":"failed"},"claims":[{"family":family,"status":"failed","facts":[],"reason":"Unexpected per-company processing error"} for family in FAMILIES],"evidence":[],"source_snapshots":[],"errors":[{"type":"internal_error","message":str(exc)}],"changes":[],"summary":{"text":"No verified company facts are available because processing failed.","evidence_ids":[],"unknown_families":list(FAMILIES),"method":"deterministic evidence-grounded summary"},"run":{"id":str(uuid.uuid4()),"started_at":failed_at,"finished_at":failed_at,"status":"complete"},"operations":{"requests":0,"runtime_ms":0,"third_party_cost_usd":0.0}}
        finally:
            worker_agent.db.close()
    with args.output.open("w",encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=(1 if judge else args.workers)) as pool:
          for result in pool.map(process,orgs):
            out.write(json.dumps(result,ensure_ascii=False)+"\n"); out.flush(); n+=1
    agent.db.close()
    dashboard=make_dashboard(args.output)
    states: dict[str,int] = {}
    families: dict[str,dict[str,int]] = {}
    requests = runtime = 0
    errors = 0
    costs = 0.0
    cost_known = True
    for row in read_jsonl_records(args.output):
        anchor=(row.get("identity_anchor") or {}).get("status","unknown")
        states[anchor]=states.get(anchor,0)+1
        requests += (row.get("operations") or {}).get("requests",0)
        runtime += (row.get("operations") or {}).get("runtime_ms",0)
        row_cost = (row.get("operations") or {}).get("third_party_cost_usd",0)
        if row_cost is None:
            cost_known = False
        else:
            costs += row_cost
        errors += len(row.get("errors",[]))
        for c in row.get("claims",[]):
            bucket=families.setdefault(c.get("family","unknown"),{})
            status=c.get("status","unknown"); bucket[status]=bucket.get(status,0)+1
    report={"input_count":len(orgs),"output_count":n,"unique_input_count":len(set(orgs)),"all_inputs_have_terminal_result":n==len(orgs),"identity_status_counts":states,"family_status_counts":families,"requests":requests,"runtime_ms_sum":runtime,"wall_runtime_ms":int((time.monotonic()-started_batch)*1000),"workers":1 if judge else args.workers,"errors":errors,"third_party_cost_usd":round(costs,6) if cost_known else None,"third_party_cost_status":"measured" if cost_known else "unknown; check provider billing","cost_basis":"no paid provider calls in default mode; opt-in provider billing is not available to this process","output":str(args.output),"dashboard":str(dashboard)}
    args.output.with_suffix(args.output.suffix+".report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
