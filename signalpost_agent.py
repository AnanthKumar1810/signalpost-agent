"""Evidence-first Signalpost runner. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import copy
import email.utils
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
import threading
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser
from nav_jobs import NavJobFeed
from public_ats import fetch_linked_jobs
from wikidata_websites import WikidataWebsiteDiscovery
from brave_websites import BraveWebsiteDiscovery
from tinyfish_websites import TinyFishWebsiteDiscovery
from llm_summary import LLMSummaryAgent

BRREG = "https://data.brreg.no/enhetsregisteret/api"
# Query BRREG production. The PPE host is pre-production and has no service guarantee.
ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap"
CONTACT = re.sub(r"[^A-Za-z0-9@.+_-]", "", os.environ.get("SIGNALPOST_CONTACT", "contact-not-configured"))[:120]
UA = f"SignalpostResearchAgent/0.2 (contact: {CONTACT})"
STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
FAMILIES = ("company_details", "people", "locations", "financials", "jobs", "public_activity", "web_presence")
_FIXED_PROVIDER_HOSTS = {
    "data.brreg.no", "query.wikidata.org", "arbeidsplassen.nav.no",
    "api.search.brave.com", "api.search.tinyfish.ai", "api.openai.com",
}
_DNS_CACHE: dict[tuple[str, int], tuple[float, list[tuple[Any, ...]]]] = {}
_DNS_CACHE_LOCK = threading.Lock()
_DNS_CACHE_TTL_SECONDS = 300


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


_LEGAL_FORM_SUFFIXES = {"as", "asa", "ans", "da", "enk", "nuf", "sa", "ba", "sam", "iks", "sti"}


def _normalized_name_tokens(value: str) -> list[str]:
    value = value.translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()
    return re.findall(r"[a-z0-9]+", value)


def legal_name_matches_page(legal_name: str, page_text: str, identity_heading: str = "") -> bool:
    """Match the full registered name in page text, optionally without legal form.

    This is used only on a BRREG-listed or identifier-discovered domain. The
    suffix-stripped form requires at least two distinctive tokens; short names
    still need the exact organisation number to avoid weak matches.
    """
    name_tokens = _normalized_name_tokens(legal_name)
    page_tokens = _normalized_name_tokens(page_text)
    if not name_tokens or not page_tokens:
        return False
    heading_tokens = _normalized_name_tokens(identity_heading)
    if len(name_tokens) == 1 and len(name_tokens[0]) >= 4:
        if any(heading_tokens[index:index + 1] == name_tokens for index in range(len(heading_tokens))):
            return True
    elif len(name_tokens) >= 2 and any(
        heading_tokens[index:index + len(name_tokens)] == name_tokens
        for index in range(max(0, len(heading_tokens) - len(name_tokens) + 1))
    ):
        return True

    # Full-page references can be boilerplate or even explicit denials. Use a
    # body-only name match only when the distinctive part has at least two
    # substantive words. A short name must appear in the identity heading or be
    # corroborated by the exact organisation number.
    distinctive = name_tokens[:-1] if name_tokens[-1] in _LEGAL_FORM_SUFFIXES else name_tokens
    if len(distinctive) == 1 and len(distinctive[0]) >= 4:
        if heading_tokens == distinctive:
            return True
        # For short, single-word brands, require two exact whole-word mentions
        # on the already registry-linked/identifier-discovered site.
        occurrences = sum(1 for token in page_tokens if token == distinctive[0])
        return occurrences >= 2
    if len(distinctive) < 2 or not all(len(token) >= 3 for token in distinctive):
        return False
    candidates = [name_tokens]
    if name_tokens[-1] in _LEGAL_FORM_SUFFIXES:
        stripped = name_tokens[:-1]
        if len(stripped) >= 2 and all(len(token) >= 3 for token in stripped):
            candidates.append(stripped)
    for candidate in candidates:
        width = len(candidate)
        if width <= len(page_tokens) and any(
            page_tokens[index:index + width] == candidate
            for index in range(len(page_tokens) - width + 1)
        ):
            return True
    return False


def page_has_organisation_number(page_text: str, organisation_number: str) -> bool:
    """Recognize the exact nine-digit identifier with common display spacing."""
    expected = normalize_org(organisation_number)
    for candidate in re.findall(r"(?<!\d)\d(?:[\s.\-]*\d){8}(?!\d)", page_text):
        if re.sub(r"\D", "", candidate) == expected:
            return True
    return False


def domain_matches_legal_name(host: str, legal_name: str) -> bool:
    """Require open-web candidates to resemble a distinctive legal-name token."""
    labels = host.lower().rstrip(".").split(".")
    host_key = "".join(_normalized_name_tokens(" ".join(
        label for label in labels[:-1] if label not in {"www", "m", "no", "com", "co"}
    )))
    tokens = [token for token in _normalized_name_tokens(legal_name)
              if token not in _LEGAL_FORM_SUFFIXES and len(token) >= 4]
    if not host_key or not tokens:
        return False
    return any(token in host_key for token in tokens) or "".join(tokens) in host_key


def official_profile_links(links: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Expose social/profile URLs only when linked by the verified company site; never crawl them."""
    platforms = {
        "linkedin.com": "LinkedIn", "facebook.com": "Facebook", "instagram.com": "Instagram",
        "x.com": "X", "twitter.com": "X", "youtube.com": "YouTube", "tiktok.com": "TikTok",
    }
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, _caption in links:
        try:
            parsed = urllib.parse.urlsplit(href)
            host = (parsed.hostname or "").lower().rstrip(".")
            platform = next((label for domain, label in platforms.items() if host == domain or host.endswith("." + domain)), None)
            if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or not platform or len([part for part in parsed.path.split("/") if part]) < 1):
                continue
            url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
            if url not in seen:
                seen.add(url)
                results.append({"platform": platform, "url": url})
        except ValueError:
            continue
    return results[:12]


def _iso_date(value: Any) -> str | None:
    """Return a calendar date only when a source value is a valid ISO YYYY-MM-DD string."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def classify_job_page(page_text: str, published_dates: list[str], reference_date: date | None = None) -> dict[str, Any] | None:
    """Classify a first-party vacancy without claiming freshness we cannot prove."""
    reference_date = reference_date or datetime.now(timezone.utc).date()
    closed = bool(re.search(
        r"position (?:is )?closed|applications? (?:are )?closed|no longer accepting|"
        r"stillingen er besatt|s(?:ø|o)knadsfristen er utl(?:ø|o)pt|utl(?:ø|o)pt frist",
        page_text, re.I,
    ))
    accepting = bool(re.search(
        r"\bapply(?:\s+now)?\b|\bsubmit\s+(?:your\s+)?application\b|"
        r"\bs(?:ø|o)k(?:\s+n(?:å|a)|\s+p(?:å|a)\s+stillingen)?\b|"
        r"\bsend\b.{0,80}\b(?:s(?:ø|o)knad|application)\b",
        page_text, re.I,
    ))
    if closed or not accepting:
        return None

    published: list[date] = []
    deadlines: list[date] = []
    for raw in published_dates:
        parsed = _iso_date(str(raw)[:10])
        if parsed:
            published.append(date.fromisoformat(parsed))
    date_pattern = r"(20[0-9]{2}-[01][0-9]-[0-3][0-9]|[0-3]?[0-9][./][01]?[0-9][./]20[0-9]{2})"
    for match in re.finditer(
        rf"(published|posted|publisert|publiseringsdato|application deadline|deadline|s(?:ø|o)knadsfrist|frist)"
        rf"[^0-9]{{0,30}}{date_pattern}", page_text, re.I,
    ):
        raw = match.group(2)
        try:
            parsed_date = (date.fromisoformat(raw) if "-" in raw else
                           datetime.strptime(raw.replace("/", "."), "%d.%m.%Y").date())
            (deadlines if re.search(r"deadline|frist", match.group(1), re.I) else published).append(parsed_date)
        except ValueError:
            continue
    if deadlines and max(deadlines) < reference_date:
        return None
    future = [value for value in deadlines if value >= reference_date]
    recent = [value for value in published if reference_date - timedelta(days=183) <= value <= reference_date]
    if future or recent:
        valid = max(future or recent)
        return {"field": "active_job_title", "freshness": "confirmed", "valid_at": valid.isoformat()}
    return {"field": "advertised_job_title", "freshness": "unconfirmed", "valid_at": None}


def is_generic_careers_title(title: str) -> bool:
    """A career landing page is evidence of a careers page, not a vacancy."""
    normalized = re.sub(r"[^\wæøå]+", " ", title.casefold()).strip()
    return bool(re.fullmatch(
        r"(?:ledige stillinger|ledig stilling|jobb(?:er|muligheter)?|karriere|"
        r"careers?|jobs?|job openings|open positions|vacancies|work with us|"
        r"arbeid hos oss|join our team)", normalized,
    ))


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


def resolve_connection_addresses(host: str, port: int) -> list[tuple[Any, ...]]:
    """Cache only fixed provider DNS answers after confirming every IP is public."""
    normalized = host.lower().rstrip(".")
    key = (normalized, port)
    if normalized in _FIXED_PROVIDER_HOSTS:
        with _DNS_CACHE_LOCK:
            cached = _DNS_CACHE.get(key)
            if cached and time.monotonic() - cached[0] < _DNS_CACHE_TTL_SECONDS:
                return list(cached[1])
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError("hostname did not resolve to an address")
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise OSError("hostname resolved to a non-public address")
    if normalized in _FIXED_PROVIDER_HOSTS:
        with _DNS_CACHE_LOCK:
            _DNS_CACHE[key] = (time.monotonic(), list(addresses))
    return addresses


def get_json(url: str, timeout: float = 12, on_retry: Any = None) -> tuple[Any, bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    host=urllib.parse.urlparse(url).hostname or ""
    with open_safe(req, host, timeout, on_retry=on_retry) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response exceeds 2 MB safety limit")
        return json.loads(raw.decode("utf-8")), raw, response.status


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, host: str, allow_cross_host: bool = False):
        self.host = host.lower().rstrip(".")
        self.allow_cross_host = allow_cross_host

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        parsed = urllib.parse.urlparse(newurl)
        target_host = (parsed.hostname or "").lower().rstrip(".")
        # A BRREG-listed legacy domain may redirect to a replacement company
        # domain. Follow that redirect only in the explicit registry path, keep
        # every request encrypted, and retain the public-address pinning check.
        # Some legacy hosts emit an HTTP redirect even though the replacement
        # host supports HTTPS; upgrade it before making the request rather than
        # sending any plaintext request.
        if self.allow_cross_host and parsed.scheme == "http" and parsed.port is None:
            parsed = parsed._replace(scheme="https")
            newurl = urllib.parse.urlunparse(parsed)
        if (parsed.scheme != "https" or not target_host or parsed.port not in (None, 443)
                or parsed.username or parsed.password or not public_host(target_host)
                or (target_host != self.host and not self.allow_cross_host)):
            raise ValueError("cross-domain or unsafe redirect rejected")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked public IP while keeping TLS verification on the hostname."""
    def connect(self) -> None:
        if self._tunnel_host:
            raise OSError("HTTPS proxy tunnels are disabled for safe company-site requests")
        port = self.port or 443
        addresses = resolve_connection_addresses(self.host, port)
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


def open_safe(req: urllib.request.Request, host: str, timeout: float, on_retry: Any = None,
              allow_cross_host_redirect: bool = False) -> Any:
    # Disable ambient HTTP(S)_PROXY settings so the checked address is the peer
    # that receives the connection; the custom HTTPS connection pins that peer.
    # These are idempotent GETs, so make one bounded retry for transient failures.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SameHostRedirect(host, allow_cross_host=allow_cross_host_redirect),
        PinnedHTTPSHandler(),
    )
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
        self.feed_links: list[str] = []
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
        if tag == "link" and a.get("href"):
            rel = (a.get("rel") or "").lower().split()
            media_type = (a.get("type") or "").lower().split(";")[0]
            if "alternate" in rel and media_type in {"application/rss+xml", "application/atom+xml"}:
                self.feed_links.append(urllib.parse.urljoin(self.base, a["href"] or ""))
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
            self.website(org, site, name or "", env, claim, allow_registered_redirect=True)
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

    def website(self, org: str, site: str, legal_name: str, env: dict[str, Any], claim: Any,
                allow_registered_redirect: bool = False, require_domain_name_match: bool = False) -> None:
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
                with open_safe(robots_req, parsed.hostname, self.timeout, on_retry=count_retry,
                               allow_cross_host_redirect=allow_registered_redirect) as robots_response:
                    final_robots = urllib.parse.urlparse(robots_response.geturl())
                    if final_robots.hostname != parsed.hostname and not allow_registered_redirect:
                        raise ValueError("cross-domain robots redirect rejected")
                    if final_robots.hostname != parsed.hostname:
                        parsed = urllib.parse.urlparse(urllib.parse.urlunparse((
                            "https", final_robots.netloc, parsed.path or "/", "", "", ""
                        )))
                        url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                        robots_url = robots_response.geturl()
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
            # A registry-declared or exact-identifier-discovered domain is a lead,
            # not identity proof. Require the full legal name (including or
            # excluding its legal-form suffix) somewhere on the page, or the exact
            # organisation number. This recovers footer/about-page identities
            # without accepting partial name overlap.
            identity_heading = " ".join(p.title + p.h1)
            name_match = legal_name_matches_page(legal_name, page_text, identity_heading)
            org_match = page_has_organisation_number(page_text, org)
            domain_match = domain_matches_legal_name(parsed.hostname or "", legal_name)
            snap=self.snapshot(org,"website",url,body,{"title":title,"text":page_text[:8000]})
            env["source_snapshots"].append(snap)
            if snap["changed"]: env["changes"].append({"family":"web_presence","source_id":snap["id"],"previous_sha256":snap["previous_sha256"],"sha256":snap["sha256"],"changed_fields":snap["changed_fields"]})
            profile_links = official_profile_links(p.links)
            profile_excerpt = " Company-site profile links: " + ", ".join(item["url"] for item in profile_links) if profile_links else ""
            ev={"id":snap["id"],"source_id":snap["id"],"excerpt":(title+" — "+page_text[:380]+profile_excerpt)[:600],"claim_fields":["website_url","website_title","website_description","company_profile_url"]}
            env["evidence"].append(ev)
            if (name_match or org_match) and (not require_domain_name_match or domain_match):
                facts=[{"field":"website_url","value":url,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None},
                       {"field":"website_title","value":title or urllib.parse.urlparse(url).hostname,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None}]
                if p.meta_description: facts.append({"field":"website_description","value":p.meta_description,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None})
                facts.extend({"field":"company_profile_url","value":profile_link,"evidence_ids":[snap["id"]],"observed_at":snap["retrieved_at"],"valid_at":None} for profile_link in profile_links)
                claim("web_presence","available",facts)
                job_pages=[]
                ats_links=[href for href,_caption in p.links]
                for href,caption in p.links:
                    full=urllib.parse.urlparse(href)
                    if full.hostname==parsed.hostname and full.scheme=="https" and re.search(r"vacan|job|career|position|stilling|ledig",full.path+" "+caption,re.I):
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
                    ats_links.extend(href for href,_caption in job_parser.links)
                    job_title=" ".join(job_parser.h1).strip() or " ".join(job_parser.title).strip()
                    job_words=" ".join(job_parser.text)
                    job_state = classify_job_page(job_words, job_parser.published_dates)
                    if job_title and (job_state or is_generic_careers_title(job_title)):
                        job_snap=self.snapshot(org,"jobs",job_url,job_body,{"title":job_title,"text":job_words[:3000]})
                        env["source_snapshots"].append(job_snap)
                        if job_snap["changed"]: env["changes"].append({"family":"jobs","source_id":job_snap["id"],"previous_sha256":job_snap["previous_sha256"],"sha256":job_snap["sha256"],"changed_fields":job_snap["changed_fields"]})
                        generic_careers = is_generic_careers_title(job_title)
                        job_field = "careers_page_url" if generic_careers else job_state["field"]
                        env["evidence"].append({"id":job_snap["id"],"source_id":job_snap["id"],"excerpt":(job_title+" — "+job_words[:500])[:600],"claim_fields":[job_field]})
                        job_value = job_url if generic_careers else {"title":job_title,"freshness":job_state["freshness"]}
                        job_facts.append({"field":job_field,"value":job_value,"evidence_ids":[job_snap["id"]],"observed_at":job_snap["retrieved_at"],"valid_at":None if generic_careers else job_state["valid_at"],"source_url":job_url})
                # Follow only public ATS boards explicitly linked from the company
                # page already verified against this legal entity. The two-source
                # evidence chain preserves that relationship and the current listing.
                def fetch_ats_json(api_url: str) -> tuple[Any, bytes, int]:
                    api_host = urllib.parse.urlparse(api_url).hostname or ""
                    env["operations"]["requests"] += 1
                    return get_json(api_url, self.timeout, on_retry=count_retry)
                ats = fetch_linked_jobs(ats_links, fetch_ats_json)
                for ats_error in ats["errors"]:
                    env["errors"].append({"type": "public_ats_error", **ats_error})
                if ats["jobs"]:
                    board_links = list(dict.fromkeys(job["board_url"] for job in ats["jobs"]))
                    link_snap = self.snapshot(org, "jobs", url, body, {"linked_public_ats_boards": board_links})
                    env["source_snapshots"].append(link_snap)
                    env["evidence"].append({"id": link_snap["id"], "source_id": link_snap["id"],
                                            "excerpt": "Verified company website explicitly links to public job board(s): " + ", ".join(board_links),
                                            "claim_fields": ["active_job"]})
                    response_snapshots: dict[tuple[str, str], str] = {}
                    for response_data in ats["responses"]:
                        response_snap = self.snapshot(org, "jobs", response_data["url"], response_data["raw"],
                                                      {"provider": response_data["board"]["provider"], "job_count": len(response_data["jobs"])})
                        env["source_snapshots"].append(response_snap)
                        response_snapshots[(response_data["board"]["provider"], response_data["board"]["link"])] = response_snap["id"]
                        if response_snap["changed"]:
                            env["changes"].append({"family": "jobs", "source_id": response_snap["id"],
                                                   "previous_sha256": response_snap["previous_sha256"], "sha256": response_snap["sha256"],
                                                   "changed_fields": response_snap["changed_fields"]})
                    for job in ats["jobs"]:
                        api_evidence_id = response_snapshots.get((job["provider"], job["board_url"]))
                        if not api_evidence_id:
                            continue
                        env["evidence"].append({"id": api_evidence_id, "source_id": api_evidence_id,
                                                "excerpt": f"Published {job['provider']} listing: {job['title']}" + (f" — {job['location']}" if job["location"] else ""),
                                                "claim_fields": ["active_job"]})
                        job_value = {key: job.get(key) for key in ("title", "location", "url", "published_at", "source_updated_at", "provider") if job.get(key)}
                        job_facts.append({"field": "active_job", "value": job_value,
                                          "evidence_ids": [link_snap["id"], api_evidence_id],
                                          "observed_at": now(), "valid_at": _iso_date(job.get("published_at"))})
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
                    exact_name = legal_name_matches_page(legal_name, activity_text, activity_heading)
                    exact_org = page_has_organisation_number(activity_text, org)
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
                # Prefer a feed the company site explicitly advertises. This
                # improves coverage on sites whose homepage links do not expose
                # individual articles, while keeping requests on the registered
                # HTTPS host and honoring robots.txt.
                for feed_url in list(dict.fromkeys(p.feed_links))[:2]:
                    feed_parsed = urllib.parse.urlparse(feed_url)
                    if (feed_parsed.scheme != "https" or feed_parsed.hostname != parsed.hostname
                            or not parser.can_fetch(UA, feed_url)):
                        continue
                    env["operations"]["requests"] += 1
                    feed_req = urllib.request.Request(feed_url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
                    try:
                        with open_safe(feed_req, parsed.hostname, self.timeout, on_retry=count_retry) as feed_response:
                            if urllib.parse.urlparse(feed_response.geturl()).hostname != parsed.hostname:
                                raise ValueError("cross-domain activity-feed redirect rejected")
                            feed_body = feed_response.read(300_001)
                            if len(feed_body) > 300_000:
                                raise ValueError("activity feed exceeds 300 KB safety limit")
                            if feed_response.headers.get_content_type() not in ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml"):
                                continue
                    except Exception as feed_exc:
                        env["errors"].append({"source": feed_url, "type": "activity_feed_error", "message": str(feed_exc)[:240]})
                        continue
                    try:
                        root = ET.fromstring(feed_body)
                    except ET.ParseError:
                        continue
                    feed_snap = self.snapshot(org, "public_activity", feed_url, feed_body, {"feed": "company-advertised RSS/Atom", "items": len(root)})
                    env["source_snapshots"].append(feed_snap)
                    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
                    feed_excerpts = []
                    for entry in entries[:10]:
                        parts = {node.tag.rsplit("}", 1)[-1].lower(): node for node in entry}
                        title = " ".join((parts.get("title").itertext() if parts.get("title") is not None else []))
                        title = " ".join(title.split())[:240]
                        if not title:
                            continue
                        date_text = ""
                        for date_key in ("published", "pubdate", "updated", "date"):
                            if parts.get(date_key) is not None:
                                date_text = " ".join(parts[date_key].itertext()).strip()
                                if date_text:
                                    break
                        published = _iso_date(date_text[:10])
                        if not published and date_text:
                            try:
                                published = email.utils.parsedate_to_datetime(date_text).date().isoformat()
                            except (TypeError, ValueError, OverflowError):
                                published = None
                        if not published:
                            continue
                        item_url = ""
                        if parts.get("link") is not None:
                            link_node = parts["link"]
                            item_url = link_node.attrib.get("href", "") or (link_node.text or "").strip()
                        item_url = urllib.parse.urljoin(feed_url, item_url)
                        item_parsed = urllib.parse.urlparse(item_url)
                        if item_parsed.scheme != "https" or item_parsed.hostname != parsed.hostname:
                            continue
                        feed_excerpts.append(f"{published} — {title}")
                        activity_facts.append({"field": "company_activity", "value": {"title": title, "published_at": published, "article_url": item_url}, "evidence_ids": [feed_snap["id"]], "observed_at": feed_snap["retrieved_at"], "valid_at": published, "source_url": feed_url})
                    if feed_excerpts:
                        env["evidence"].append({"id": feed_snap["id"], "source_id": feed_snap["id"], "excerpt": "Company-advertised activity feed: " + " | ".join(feed_excerpts)[:550], "claim_fields": ["public_activity"]})
                claim("public_activity", "available" if activity_facts else "not_available", activity_facts, None if activity_facts else "No dated, entity-matched company news or activity page was verified")
            else:
                reason = ("Open-web candidate domain did not match a distinctive legal-name token" if
                          require_domain_name_match and not domain_match else
                          "Website does not independently identify the exact legal entity")
                claim("web_presence","ambiguous",reason=reason)
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
        clauses = [f"{name} is registered in Norway" + (f" as {form}" if form else "") + "."]
        evidence_ids: set[str] = set()
        def cite(fact: dict[str, Any] | None) -> None:
            if fact:
                evidence_ids.update(eid for eid in fact.get("evidence_ids", []) if isinstance(eid, str))
        for fact in details:
            cite(fact)
        if industry:
            clauses.append(f"BRREG classifies its registered activity as {industry}.")
        if city:
            clauses.append(f"Its registered business location is {city}.")
        employees = fields.get("employee_count")
        if employees and isinstance(employees.get("value"), int) and not isinstance(employees.get("value"), bool):
            clauses.append(f"BRREG reports {employees['value']} registered employees.")
            cite(employees)
        if fields.get("bankruptcy", {}).get("value") is True:
            clauses.append("The registry marks it as bankrupt.")
        if fields.get("under_liquidation", {}).get("value") is True:
            clauses.append("The registry marks it as under liquidation.")
        if fields.get("under_forced_liquidation", {}).get("value") is True:
            clauses.append("The registry marks it as under forced liquidation or dissolution.")

        web_facts = by_family.get("web_presence", {}).get("facts", [])
        website_title = next((fact for fact in web_facts if fact.get("field") == "website_title"), None)
        if website_title and isinstance(website_title.get("value"), str):
            clauses.append(f"The verified website page title is {website_title['value']}.")
            cite(website_title)
        company_profiles = [fact for fact in web_facts if fact.get("field") == "company_profile_url"
                            and isinstance(fact.get("value"), dict) and fact["value"].get("platform")]
        if company_profiles:
            platforms = sorted({fact["value"]["platform"] for fact in company_profiles})
            clauses.append("The verified company website links to public profiles on " + ", ".join(platforms) + ".")
            for fact in company_profiles:
                cite(fact)

        role_facts = by_family.get("people", {}).get("facts", [])
        leaders = [fact for fact in role_facts if isinstance(fact.get("value"), dict)
                   and re.search(r"director|manager|chair|leder|daglig|styreleder", str(fact["value"].get("role") or ""), re.I)
                   and fact["value"].get("name")]
        if leaders:
            selected_leaders = leaders[:3]
            clauses.append("BRREG lists registered leadership roles: " + "; ".join(
                f"{fact['value'].get('role')}: {fact['value']['name']}" for fact in selected_leaders) + ".")
            for fact in selected_leaders:
                cite(fact)

        financial_facts = by_family.get("financials", {}).get("facts", [])
        periods = [fact for fact in financial_facts if isinstance(fact.get("valid_at"), str)]
        latest_period = max((fact.get("valid_at") for fact in periods), default=None)
        if latest_period:
            key_figures = {fact.get("field"): fact for fact in periods if fact.get("valid_at") == latest_period}
            parts = []
            for field, label in (("revenue_nok", "revenue"), ("operating_result_nok", "operating result"), ("net_result_nok", "net result")):
                fact = key_figures.get(field)
                if fact and isinstance(fact.get("value"), (int, float)) and not isinstance(fact.get("value"), bool):
                    parts.append(f"{label} NOK {fact['value']:,.0f}")
                    cite(fact)
            if parts:
                clauses.append(f"The latest available annual accounts end {latest_period}: " + ", ".join(parts) + ".")

        activity = by_family.get("public_activity", {}).get("facts", [])
        dated_activity = [fact for fact in activity if isinstance(fact.get("value"), dict)
                          and isinstance(fact["value"].get("published_at"), str)]
        if dated_activity:
            latest_activity = max(dated_activity, key=lambda fact: fact["value"]["published_at"])
            value = latest_activity["value"]
            clauses.append(f"Latest verified public activity: {value.get('title', 'dated company update')} ({value['published_at']}).")
            cite(latest_activity)

        jobs = by_family.get("jobs", {}).get("facts", [])
        active_job_titles = []
        undated_job_titles = []
        for fact in jobs:
            if fact.get("field") == "careers_page_url":
                cite(fact)
                continue
            value = fact.get("value")
            title = value.get("title") if isinstance(value, dict) else value
            if isinstance(title, str) and title.strip():
                if str(fact.get("field") or "").startswith("active_job"):
                    active_job_titles.append(title.strip())
                else:
                    undated_job_titles.append(title.strip())
                cite(fact)
        if active_job_titles:
            clauses.append("Verified current vacancies include " + ", ".join(dict.fromkeys(active_job_titles[:3])) + ".")
        if undated_job_titles:
            clauses.append("The verified company website advertises " + ", ".join(dict.fromkeys(undated_job_titles[:3])) + ", but its current publication or deadline date could not be confirmed.")
        if any(fact.get("field") == "careers_page_url" for fact in jobs):
            clauses.append("A verified company careers page was found, but it does not establish a current vacancy.")

        changes = env.get("changes", [])
        changed_families = sorted({item.get("family") for item in changes if isinstance(item, dict) and item.get("family")})
        if changed_families:
            clauses.append("This refresh detected source changes in " + ", ".join(changed_families) + "; see the change log and dated evidence.")

        unknowns = [family for family in FAMILIES if by_family.get(family, {}).get("status") not in {"available", "not_applicable"}]
        if unknowns:
            labels = ", ".join(family.replace("_", " ") for family in unknowns)
            clauses.append("No verified result is available for: " + labels + ". These gaps may reflect missing, blocked, or inaccessible sources, not proof that the information does not exist.")
        return {"text": " ".join(clauses), "evidence_ids": sorted(evidence_ids), "unknown_families": unknowns,
                "method": "deterministic evidence-grounded profile synthesis"}


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
:root{font:16px/1.5 system-ui,sans-serif;color:#17232b;background:#f3f6f5}body{margin:0}header{background:#123c44;color:white;padding:1.2rem max(1rem,calc((100vw - 1200px)/2))}main{max-width:1200px;margin:1rem auto;padding:0 1rem}.bar{display:flex;gap:.65rem;align-items:center;flex-wrap:wrap}.bar input,.bar select,.bar button{padding:.68rem;border:1px solid #aab8ba;border-radius:.5rem;background:white;font:inherit}.bar input{flex:1;min-width:14rem}.bar button{cursor:pointer}.layout{display:grid;grid-template-columns:minmax(17rem,.8fr) minmax(0,1.5fr);gap:1rem;margin-top:1rem}.list,.detail{background:white;border:1px solid #d8e0e1;border-radius:.7rem;padding:.85rem}.list{max-height:78vh;overflow:auto}.companyrow{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:.4rem;border-bottom:1px solid #e5ebeb;padding:.5rem 0}.company{display:block;width:100%;text-align:left;background:white;border:0;padding:.3rem;font:inherit;cursor:pointer;overflow-wrap:anywhere}.company:hover,.company:focus{background:#edf6f5;outline:2px solid #367b70}.muted{color:#58686c;font-size:.9rem}.status{display:inline-block;border-radius:99px;padding:.1rem .5rem;background:#e5efed;font-size:.8rem}.claim{border-top:1px solid #e5ebeb;padding:.65rem 0}.claim h3{margin:.2rem 0;font-size:1rem;text-transform:capitalize}.fact{margin:.35rem 0;overflow-wrap:anywhere}.source{font-size:.85rem;overflow-wrap:anywhere}.empty{padding:1rem;color:#58686c}.tablewrap{overflow-x:auto}.compare{border-collapse:collapse;min-width:600px;width:100%}.compare th,.compare td{padding:.55rem;text-align:left;vertical-align:top;border-bottom:1px solid #d8e0e1}.compare th:first-child{position:sticky;left:0;background:#f3f6f5}.summary{background:#edf6f5;border-left:4px solid #367b70;padding:.8rem;border-radius:.25rem}@media(max-width:720px){.layout{grid-template-columns:1fr}.list{max-height:36vh}.bar input{min-width:100%}.bar select{flex:1}.detail{padding:.7rem}}
</style><header><h1>Signalpost company profiles</h1><div>Search, compare and verify evidence for this company batch</div></header><main><p id="overview" class="summary" aria-live="polite"></p><div class="bar"><input id="q" type="search" placeholder="Search company name or organisation number" aria-label="Search companies"><select id="state" aria-label="Filter by result status"><option value="all">All profiles</option><option value="available">Has verified findings</option><option value="gaps">Has unavailable information</option><option value="errors">Has source or processing errors</option></select><select id="sort" aria-label="Sort companies"><option value="name">Sort by company name</option><option value="coverage">Sort by available categories</option></select><button id="compare" type="button">Compare selected</button><button id="clear" type="button">Clear selection</button><span id="count" class="muted"></span></div><div class="layout"><nav id="companies" class="list" aria-label="Companies"></nav><section id="profile" class="detail" aria-live="polite"><p class="empty">Choose a company to inspect its facts and evidence. Select up to three companies to compare.</p></section></div></main>
<script>const rows=JSON.parse('''+json.dumps(embedded)+''');const list=document.querySelector('#companies'),panel=document.querySelector('#profile'),query=document.querySelector('#q'),count=document.querySelector('#count'),state=document.querySelector('#state'),sort=document.querySelector('#sort'),selected=new Set();const dataGroups=[['company_details'],['financials'],['people','locations'],['web_presence'],['jobs','public_activity']];function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}function groupCount(r){return dataGroups.filter(group=>group.some(family=>(r.claims||[]).some(c=>c.family===family&&c.status==='available'))).length}function verifiedCount(r){return (r.claims||[]).filter(c=>c.status==='available').length}document.querySelector('#overview').textContent=rows.length+' company profiles · '+rows.filter(r=>groupCount(r)===5).length+' have verified findings in all five information areas.';function displayValue(v){if(v===null||v===undefined)return '—';if(typeof v==='object')return JSON.stringify(v);return String(v)}function safeLink(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:null}catch{return null}}function show(row){panel.replaceChildren();const id=row.identity_anchor||{};panel.append(el('h2',id.legal_name||row.organisation_number));panel.append(el('div','Org. no. '+row.organisation_number+' · '+(id.status||'unknown'),'muted'));if(row.summary?.text)panel.append(el('p',row.summary.text,'summary'));for(const c of (row.claims||[])){const section=el('article',undefined,'claim');section.append(el('h3',c.family.replaceAll('_',' ')+' · '+c.status));if(c.reason)section.append(el('p',c.reason,'muted'));for(const f of (c.facts||[])){section.append(el('p',(f.field||'fact')+': '+displayValue(f.value),'fact'));const ev=(f.evidence_ids||[]).map(x=>(row.source_snapshots||[]).find(s=>s.id===x)).filter(Boolean);for(const s of ev){const href=safeLink(s.url);if(!href)continue;const a=el('a',s.url,'source');a.href=href;a.target='_blank';a.rel='noopener noreferrer';section.append(a,el('span',' · retrieved '+(s.retrieved_at||'date unavailable'),'muted'))}}panel.append(section)}if(row.errors?.length){panel.append(el('h3','Retrieval errors'));for(const e of row.errors)panel.append(el('p',(e.type||'error')+': '+(e.message||''),'muted'))}}function compareRows(){const chosen=rows.filter(r=>selected.has(r.organisation_number)).slice(0,3);panel.replaceChildren();if(chosen.length<2){panel.append(el('p','Select at least two companies, then choose Compare selected.','empty'));return}panel.append(el('h2','Company comparison'));const wrap=el('div',undefined,'tablewrap'),table=el('table',undefined,'compare'),head=el('tr');head.append(el('th','Information'));for(const r of chosen)head.append(el('th',(r.identity_anchor?.legal_name||r.organisation_number)+' · '+r.organisation_number));const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');const fields=['summary','company_details','locations','people','financials','web_presence','jobs','public_activity'];for(const family of fields){const tr=el('tr'),label=el('th',family.replaceAll('_',' '));tr.append(label);for(const r of chosen){const td=el('td');if(family==='summary')td.textContent=r.summary?.text||'—';else{const c=(r.claims||[]).find(x=>x.family===family);if(!c)td.textContent='—';else{td.append(el('span',c.status,'status'));for(const f of c.facts||[])td.append(el('p',(f.field||'fact')+': '+displayValue(f.value),'fact'));if(c.reason)td.append(el('div',c.reason,'muted'))}}tr.append(td)}body.append(tr)}table.append(body);wrap.append(table);panel.append(wrap);panel.append(el('p','Select a company from the list to inspect its source links and retrieval dates.','muted'))}function render(){list.replaceChildren();let q=query.value.toLowerCase();let found=rows.filter(r=>{const text=((r.identity_anchor?.legal_name||'')+' '+r.organisation_number).toLowerCase();const has=verifiedCount(r)>0,err=(r.errors||[]).length>0,gap=(r.claims||[]).some(c=>['not_available','blocked','ambiguous','failed'].includes(c.status));return text.includes(q)&&(state.value==='all'||state.value==='available'&&has||state.value==='gaps'&&gap||state.value==='errors'&&err)});found.sort((a,b)=>sort.value==='coverage'?verifiedCount(b)-verifiedCount(a)||String(a.identity_anchor?.legal_name||'').localeCompare(String(b.identity_anchor?.legal_name||'')):String(a.identity_anchor?.legal_name||'').localeCompare(String(b.identity_anchor?.legal_name||'')));count.textContent=found.length+' shown of '+rows.length+' profiles · '+selected.size+' selected';for(const r of found){const row=el('div',undefined,'companyrow'),check=el('input');check.type='checkbox';check.checked=selected.has(r.organisation_number);check.setAttribute('aria-label','Select '+(r.identity_anchor?.legal_name||r.organisation_number));check.onchange=()=>{if(check.checked){if(selected.size>=3){check.checked=false;return}selected.add(r.organisation_number)}else selected.delete(r.organisation_number);render()};const button=el('button',undefined,'company');button.type='button';button.append(el('strong',r.identity_anchor?.legal_name||r.organisation_number),el('div',r.organisation_number+' · '+groupCount(r)+'/5 data areas','muted'));button.onclick=()=>show(r);row.append(check,button);list.append(row)}}query.addEventListener('input',render);state.addEventListener('change',render);sort.addEventListener('change',render);document.querySelector('#compare').onclick=compareRows;document.querySelector('#clear').onclick=()=>{selected.clear();render()};render();</script></html>'''
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
    ap.add_argument("--nav-jobs",action="store_true",help="Enable a bounded NAV public-job-feed scan over up to six months of active-ad updates")
    ap.add_argument("--nav-pages",type=int,default=25,help="Maximum NAV feed pages when --nav-jobs is enabled (default: 25; hard cap: 100)")
    ap.add_argument("--nav-details",type=int,default=100,help="Maximum NAV job details to fetch per batch (default: 100; hard cap: 500)")
    ap.add_argument("--nav-days",type=int,default=180,help="NAV update lookback in days (default: 180; hard cap: 183)")
    ap.add_argument("--wikidata-websites",action="store_true",help="Discover website candidates via exact Wikidata P2333 matches, then independently verify each site")
    ap.add_argument("--brave-websites",action="store_true",help="After BRREG and Wikidata find no website, use Brave Search for candidate URLs; requires BRAVE_SEARCH_API_KEY and a Brave plan with storage rights")
    ap.add_argument("--brave-max-requests",type=int,default=600,help="Maximum Brave requests per batch (default: 600; hard cap: 2000)")
    ap.add_argument("--tinyfish-websites",action="store_true",help="After BRREG and Wikidata find no website, use TinyFish Search for candidate URLs; requires TINYFISH_API_KEY")
    ap.add_argument("--tinyfish-max-requests",type=int,default=600,help="Maximum TinyFish Search requests per batch (default: 600; hard cap: 2000)")
    ap.add_argument("--llm-summary",action="store_true",help="Opt in to an API-key-gated evidence-grounded summary; invalid output falls back to the deterministic summary")
    args=ap.parse_args()
    if args.workers < 1 or args.workers > 16:
        ap.error("--workers must be between 1 and 16")
    if args.wikidata_websites and args.no_website:
        ap.error("--wikidata-websites requires website fetching; remove --no-website")
    if args.brave_websites and args.no_website:
        ap.error("--brave-websites requires website fetching; remove --no-website")
    if args.brave_websites and not args.wikidata_websites:
        ap.error("--brave-websites runs only after Wikidata; include --wikidata-websites")
    if args.tinyfish_websites and args.no_website:
        ap.error("--tinyfish-websites requires website fetching; remove --no-website")
    if args.tinyfish_websites and not args.wikidata_websites:
        ap.error("--tinyfish-websites runs only after Wikidata; include --wikidata-websites")
    if args.brave_websites and not 1 <= args.brave_max_requests <= 2000:
        ap.error("--brave-max-requests must be between 1 and 2000")
    if args.tinyfish_websites and not 1 <= args.tinyfish_max_requests <= 2000:
        ap.error("--tinyfish-max-requests must be between 1 and 2000")
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
    brave = None
    if args.brave_websites:
        if os.environ.get("SIGNALPOST_BRAVE_STORAGE_RIGHTS", "").strip().lower() not in {"1", "true", "yes"}:
            ap.error("Brave Search results may be retained in verified profile URLs; confirm a plan with storage rights by setting SIGNALPOST_BRAVE_STORAGE_RIGHTS=1")
        try:
            brave = BraveWebsiteDiscovery(timeout=min(args.timeout, 10), max_requests=args.brave_max_requests)
        except Exception as exc:
            ap.error(f"could not enable Brave Search discovery: {exc}")
    tinyfish = None
    if args.tinyfish_websites:
        try:
            tinyfish = TinyFishWebsiteDiscovery(timeout=min(args.timeout, 15), max_requests=args.tinyfish_max_requests)
        except Exception as exc:
            ap.error(f"could not enable TinyFish Search discovery: {exc}")
    summary_agent = None
    if args.llm_summary:
        try:
            summary_agent = LLMSummaryAgent(timeout=min(args.timeout + 15, 45))
        except Exception as exc:
            ap.error(f"could not enable LLM summary: {exc}")
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
    with ThreadPoolExecutor(max_workers=(1 if judge else args.workers)) as pool:
        results = list(pool.map(process, orgs))
    wikidata_report = {"status": "disabled", "requests": 0, "target_count": 0, "candidate_count": 0, "errors": []}
    if args.wikidata_websites and results:
        discovery = WikidataWebsiteDiscovery(timeout=min(args.timeout, 8))
        candidates, wikidata_report = discovery.discover(results)
        results[0].setdefault("operations", {}).setdefault("shared_requests", 0)
        results[0]["operations"]["shared_requests"] += wikidata_report.get("requests", 0)
        # Apply only candidates whose Wikidata entity has the exact requested
        # organisation number. P856 remains a lead: website() must independently
        # verify the fetched page before any web, jobs, or activity fact is kept.
        for result in results:
            org = str(result.get("organisation_number") or "")
            candidate = candidates.get(org)
            result["website_discovery"] = {
                "status": "candidate_found" if candidate else "not_found",
                "method": "wikidata_p2333_exact_to_p856",
                "candidate_url": candidate.get("site") if candidate else None,
            }
            if not candidate:
                if org in discovery.failed_organisations:
                    for item in result.get("claims", []):
                        if item.get("family") == "web_presence" and item.get("reason") == "No registry-listed website":
                            item["status"] = "failed"
                            item["reason"] = "Wikidata website discovery failed; no BRREG-listed website was available"
                    result.setdefault("errors", []).append({"type": "website_discovery_failed", "source": "https://query.wikidata.org/sparql", "message": "The exact-identifier website discovery request failed for this company"})
                elif result["website_discovery"]["status"] == "not_found":
                    for item in result.get("claims", []):
                        if item.get("family") == "web_presence" and item.get("reason") == "No registry-listed website":
                            item["reason"] = "No BRREG-listed website or exact-identifier Wikidata website candidate was found"
                continue
            statement = {"organisation_number": org, "wikidata_item": candidate["item"], "property": "P856", "candidate_url": candidate["site"]}
            statement_raw = json.dumps(statement, ensure_ascii=False, sort_keys=True).encode("utf-8")
            snapshot = agent.snapshot(org, "website_discovery", candidate["item"], statement_raw, statement)
            snapshot["license"] = "CC0 (Wikidata structured data)"
            result.setdefault("source_snapshots", []).append(snapshot)
            result.setdefault("evidence", []).append({
                "id": snapshot["id"], "source_id": snapshot["id"],
                "excerpt": f"Wikidata {candidate['qid']} has exact Norwegian organisation-number property P2333={org} and lists P856={candidate['site']}; candidate only, not identity proof.",
                "claim_fields": ["website_candidate"],
            })
            result["claims"] = [item for item in result.get("claims", []) if item.get("family") not in {"web_presence", "jobs", "public_activity"}]
            def add_site_claim(family: str, status: str, facts: list[dict[str, Any]] | None = None, reason: str | None = None, _result: dict[str, Any] = result) -> None:
                _result.setdefault("claims", []).append({"family": family, "status": status, "facts": facts or [], **({"reason": reason} if reason else {})})
            agent.website(org, candidate["site"], (result.get("identity_anchor") or {}).get("legal_name") or "", result, add_site_claim)
            prior_runtime = result.get("operations", {}).get("runtime_ms", 0)
            agent.finish(result, time.monotonic() - prior_runtime / 1000)
    tinyfish_report = {"status": "disabled", "requests": 0, "target_count": 0,
                       "candidate_count": 0, "verified_candidate_count": 0,
                       "estimated_cost_usd": 0.0, "errors": []}
    if tinyfish and results:
        failed_wikidata = set(wikidata_report.get("failed_organisations", []))
        candidate_count = verified_count = searched = 0
        for index, original in enumerate(results):
            org = str(original.get("organisation_number") or "")
            anchor = original.get("identity_anchor") or {}
            if anchor.get("status") != "verified" or org in failed_wikidata:
                continue
            details = next((item for item in original.get("claims", []) if item.get("family") == "company_details"), {})
            registered_site = any(fact.get("field") == "website" and fact.get("value")
                                  for fact in details.get("facts", []))
            discovery_state = original.get("website_discovery") or {}
            if registered_site or discovery_state.get("status") != "not_found":
                continue
            searched += 1
            search_result = tinyfish.discover(org, str(anchor.get("legal_name") or ""))
            attempted_web_requests = 0
            verified_trial = None
            ambiguous_candidate = blocked_candidate = False
            candidate_count += len(search_result.get("candidate_urls", []))
            for candidate_url in search_result.get("candidate_urls", []):
                trial = copy.deepcopy(original)
                trial["claims"] = [item for item in trial.get("claims", [])
                                   if item.get("family") not in {"web_presence", "jobs", "public_activity"}]
                def add_tinyfish_claim(family: str, status: str, facts: list[dict[str, Any]] | None = None,
                                       reason: str | None = None, _trial: dict[str, Any] = trial) -> None:
                    _trial.setdefault("claims", []).append({"family": family, "status": status,
                        "facts": facts or [], **({"reason": reason} if reason else {})})
                before_requests = trial.get("operations", {}).get("requests", 0)
                # Search candidates do not receive registered-site redirect
                # privileges; they must stay on the candidate host and pass the
                # normal exact-company page check.
                agent.website(org, candidate_url, str(anchor.get("legal_name") or ""), trial, add_tinyfish_claim,
                              require_domain_name_match=True)
                attempted_web_requests += max(0, trial.get("operations", {}).get("requests", 0) - before_requests)
                web_claim = next((item for item in trial.get("claims", []) if item.get("family") == "web_presence"), {})
                if web_claim.get("status") == "available":
                    verified_trial = trial
                    break
                ambiguous_candidate = ambiguous_candidate or web_claim.get("status") == "ambiguous"
                blocked_candidate = blocked_candidate or web_claim.get("status") == "blocked"
            target = verified_trial if verified_trial is not None else original
            target.setdefault("website_discovery", {}).update({
                "tinyfish_status": ("candidate_verified" if verified_trial is not None else
                                    "candidate_unverified" if search_result.get("candidate_urls") else search_result.get("status")),
                "tinyfish_method": "tinyfish_org_number_then_legal_name_candidate_search",
                "tinyfish_requests": search_result.get("requests", 0),
                "tinyfish_estimated_cost_usd": 0.0,
                "candidate_urls_retained": False,
            })
            if verified_trial is not None:
                target["website_discovery"]["status"] = "verified_candidate"
            base_requests = original.get("operations", {}).get("requests", 0)
            target.setdefault("operations", {})["requests"] = base_requests + search_result.get("requests", 0) + attempted_web_requests
            target["operations"]["tinyfish_search_estimated_cost_usd"] = 0.0
            if verified_trial is not None:
                verified_count += 1
                prior_runtime = target.get("operations", {}).get("runtime_ms", 0)
                agent.finish(target, time.monotonic() - prior_runtime / 1000)
            elif search_result.get("candidate_urls"):
                for item in target.get("claims", []):
                    if item.get("family") == "web_presence":
                        item["status"] = "not_available"
                        item["reason"] = "TinyFish returned candidate pages, but none passed company-ownership and exact-entity verification"
                    elif item.get("family") in {"jobs", "public_activity"}:
                        item["status"] = "not_available"
                        item["reason"] = "No verified company website was available for this lookup"
                if not ambiguous_candidate and not blocked_candidate:
                    target.setdefault("errors", []).append({"type": "tinyfish_candidate_verification_failed", "message": "Candidate pages could not be safely verified; no candidate facts were published"})
                prior_runtime = target.get("operations", {}).get("runtime_ms", 0)
                agent.finish(target, time.monotonic() - prior_runtime / 1000)
            results[index] = target
        tinyfish_report = {"status": "complete" if not tinyfish.errors else "partial" if tinyfish.requests else "failed",
                           "requests": tinyfish.requests, "target_count": searched,
                           "candidate_count": candidate_count, "verified_candidate_count": verified_count,
                           "estimated_cost_usd": 0.0, "candidate_urls_retained": False,
                           "pricing_note": "Search was documented as free at implementation time; verify current provider terms.",
                           "errors": tinyfish.errors}
    brave_report = {"status": "disabled", "requests": 0, "target_count": 0,
                    "candidate_count": 0, "verified_candidate_count": 0,
                    "estimated_cost_usd": 0.0, "errors": []}
    if brave and results:
        failed_wikidata = set(wikidata_report.get("failed_organisations", []))
        candidate_count = verified_count = 0
        estimated_cost = 0.0
        searched = 0
        for index, original in enumerate(results):
            org = str(original.get("organisation_number") or "")
            anchor = original.get("identity_anchor") or {}
            if anchor.get("status") != "verified" or org in failed_wikidata:
                continue
            details = next((item for item in original.get("claims", []) if item.get("family") == "company_details"), {})
            registered_site = any(fact.get("field") == "website" and fact.get("value")
                                  for fact in details.get("facts", []))
            discovery_state = original.get("website_discovery") or {}
            if registered_site or discovery_state.get("status") != "not_found":
                continue
            searched += 1
            search_result = brave.discover(org, str(anchor.get("legal_name") or ""))
            attempted_web_requests = 0
            verified_trial = None
            ambiguous_candidate = False
            blocked_candidate = False
            candidate_count += len(search_result.get("candidate_urls", []))
            for candidate_url in search_result.get("candidate_urls", []):
                trial = copy.deepcopy(original)
                trial["claims"] = [item for item in trial.get("claims", [])
                                   if item.get("family") not in {"web_presence", "jobs", "public_activity"}]
                def add_candidate_claim(family: str, status: str, facts: list[dict[str, Any]] | None = None,
                                        reason: str | None = None, _trial: dict[str, Any] = trial) -> None:
                    _trial.setdefault("claims", []).append({"family": family, "status": status,
                        "facts": facts or [], **({"reason": reason} if reason else {})})
                before_requests = trial.get("operations", {}).get("requests", 0)
                agent.website(org, candidate_url, str(anchor.get("legal_name") or ""), trial, add_candidate_claim,
                              require_domain_name_match=True)
                used_requests = trial.get("operations", {}).get("requests", 0) - before_requests
                attempted_web_requests += max(0, used_requests)
                web_claim = next((item for item in trial.get("claims", []) if item.get("family") == "web_presence"), {})
                if web_claim.get("status") == "available":
                    verified_trial = trial
                    break
                ambiguous_candidate = ambiguous_candidate or web_claim.get("status") == "ambiguous"
                blocked_candidate = blocked_candidate or web_claim.get("status") == "blocked"
            target = verified_trial if verified_trial is not None else original
            target.setdefault("website_discovery", {})
            target["website_discovery"].update({
                "brave_status": ("candidate_verified" if verified_trial is not None else
                                 "candidate_unverified" if search_result.get("candidate_urls") else search_result.get("status")),
                "brave_method": "brave_org_number_then_legal_name_candidate_search",
                "brave_requests": search_result.get("requests", 0),
                "brave_billable_requests": search_result.get("billable_requests", 0),
                "brave_estimated_cost_usd": search_result.get("estimated_cost_usd", 0.0),
                "candidate_urls_retained": False,
            })
            base_requests = original.get("operations", {}).get("requests", 0)
            target.setdefault("operations", {})["requests"] = base_requests + search_result.get("requests", 0) + attempted_web_requests
            target["operations"]["brave_search_estimated_cost_usd"] = search_result.get("estimated_cost_usd", 0.0)
            prior_estimate = target["operations"].get("third_party_cost_estimate_usd", 0.0) or 0.0
            target["operations"]["third_party_cost_estimate_usd"] = round(prior_estimate + search_result.get("estimated_cost_usd", 0.0), 6)
            if search_result.get("requests", 0):
                target["operations"]["third_party_cost_usd"] = None
            if verified_trial is not None:
                verified_count += 1
                prior_runtime = target.get("operations", {}).get("runtime_ms", 0)
                agent.finish(target, time.monotonic() - prior_runtime / 1000)
                results[index] = target
            elif search_result.get("candidate_urls"):
                for item in target.get("claims", []):
                    if item.get("family") == "web_presence":
                        item["status"] = "not_available"
                        item["reason"] = "Brave returned candidate pages, but none passed company-ownership and exact-entity verification"
                    elif item.get("family") in {"jobs", "public_activity"}:
                        item["status"] = "not_available"
                        item["reason"] = "No verified company website was available for this lookup"
                if not ambiguous_candidate:
                    if not blocked_candidate:
                        target.setdefault("errors", []).append({"type": "brave_candidate_verification_failed", "message": "Candidate pages could not be safely verified; no candidate facts were published"})
                prior_runtime = target.get("operations", {}).get("runtime_ms", 0)
                agent.finish(target, time.monotonic() - prior_runtime / 1000)
            if verified_trial is None:
                results[index] = target
            estimated_cost += search_result.get("estimated_cost_usd", 0.0)
        brave_report = {"status": "complete" if not brave.errors else "partial" if brave.requests else "failed",
                        "requests": brave.requests, "billable_requests": brave.billable_requests, "target_count": searched,
                        "candidate_count": candidate_count, "verified_candidate_count": verified_count,
                        "estimated_cost_usd": round(estimated_cost, 6),
                        "candidate_urls_retained": False, "errors": brave.errors}
    llm_summary_report = {"status": "disabled", "calls": 0, "accepted": 0,
                          "rejected_or_failed": 0, "estimated_cost_usd": 0.0}
    if summary_agent and results:
        with ThreadPoolExecutor(max_workers=min(args.workers, 4)) as pool:
            summaries = list(pool.map(summary_agent.summarize, results))
        calls = accepted = rejected = 0
        estimated_cost = 0.0
        estimate_unknown = False
        for result, generated in zip(results, summaries):
            calls += generated.get("calls_made", 0)
            if generated.get("summary"):
                previous = result.get("summary") or {}
                generated["summary"]["unknown_families"] = previous.get("unknown_families", [])
                result["summary"] = generated["summary"]
                accepted += 1
            elif generated.get("calls_made", 0):
                rejected += 1
            result["summary_agent"] = {key: generated.get(key) for key in ("status", "model", "usage", "error") if generated.get(key) is not None}
            result.setdefault("operations", {})["requests"] = result.get("operations", {}).get("requests", 0) + generated.get("calls_made", 0)
            if generated.get("calls_made", 0):
                estimate = generated.get("estimated_cost_usd")
                if isinstance(estimate, (int, float)):
                    estimated_cost += estimate
                    prior = result["operations"].get("third_party_cost_estimate_usd", 0.0) or 0.0
                    result["operations"]["third_party_cost_estimate_usd"] = round(prior + estimate, 8)
                else:
                    estimate_unknown = True
                result["operations"]["third_party_cost_usd"] = None
        llm_summary_report = {"status": "complete" if not rejected else "partial",
                              "calls": calls, "accepted": accepted,
                              "rejected_or_failed": rejected,
                              "estimated_cost_usd": None if estimate_unknown else round(estimated_cost, 8),
                              "model": summary_agent.model,
                              "cost_note": "Estimate uses the published GPT-6 Luna input/output rates; refresh model pricing before an official run."}
    nav_report = {"status": "disabled", "requests": 0, "matched_profiles": 0}
    if args.nav_jobs and results:
        nav_client = NavJobFeed(timeout=args.timeout, max_pages=args.nav_pages, max_details=args.nav_details, lookback_days=args.nav_days)
        nav_report = nav_client.enrich(results)
        # NAV is a batch-level shared source. Attribute its shared request cost
        # once, and count per-ad requests on the matching company profile.
        if results:
            results[0].setdefault("operations", {}).setdefault("shared_requests", 0)
            results[0]["operations"]["shared_requests"] += nav_report.get("requests", 0)
        for result in results:
            org = str(result.get("organisation_number") or "")
            for claim_item in result.get("claims", []):
                if claim_item.get("family") != "jobs":
                    continue
                for fact in claim_item.get("facts", []):
                    nav_url = fact.pop("_nav_url", None)
                    excerpt = fact.pop("_nav_excerpt", None)
                    if not nav_url:
                        continue
                    raw = json.dumps(fact.get("value"), ensure_ascii=False, sort_keys=True).encode("utf-8")
                    snapshot = agent.snapshot(org, "jobs", nav_url, raw, fact.get("value"))
                    result.setdefault("source_snapshots", []).append(snapshot)
                    result.setdefault("evidence", []).append({"id": snapshot["id"], "source_id": snapshot["id"], "excerpt": excerpt or "NAV active job posting", "claim_fields": ["active_job"]})
                    fact.update({"evidence_ids": [snapshot["id"]], "observed_at": snapshot["retrieved_at"], "valid_at": (fact.get("value") or {}).get("published_at"), "source_url": nav_url})
            # Run the same final provenance gate after attaching shared-source facts.
            prior_runtime = result.get("operations", {}).get("runtime_ms", 0)
            agent.finish(result, time.monotonic() - prior_runtime / 1000)
        agent.db.commit()
    with args.output.open("w",encoding="utf-8") as out:
        for result in results:
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
        requests += (row.get("operations") or {}).get("requests",0) + (row.get("operations") or {}).get("shared_requests",0)
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
    estimate_total = sum((row.get("operations") or {}).get("third_party_cost_estimate_usd", 0.0) or 0.0 for row in read_jsonl_records(args.output))
    cost_status = ("provider charge unavailable; see estimate and usage records" if not cost_known else
                   "provider-published free tier; billing not independently verified" if tinyfish_report.get("requests", 0) else
                   "no paid provider calls")
    report={"input_count":len(orgs),"output_count":n,"unique_input_count":len(set(orgs)),"all_inputs_have_terminal_result":n==len(orgs),"identity_status_counts":states,"family_status_counts":families,"requests":requests,"nav_job_feed":nav_report,"wikidata_website_discovery":wikidata_report,"tinyfish_website_discovery":tinyfish_report,"brave_website_discovery":brave_report,"llm_summary":llm_summary_report,"runtime_ms_sum":runtime,"wall_runtime_ms":int((time.monotonic()-started_batch)*1000),"workers":1 if judge else args.workers,"errors":errors,"third_party_cost_usd":round(costs,6) if cost_known else None,"third_party_cost_estimate_usd":round(estimate_total,8),"third_party_cost_status":cost_status,"cost_basis":"default run makes no paid calls; TinyFish Search was documented as free when integrated; optional Brave/OpenAI costs are estimates from published rates and response usage, not billing records","output":str(args.output),"dashboard":str(dashboard)}
    args.output.with_suffix(args.output.suffix+".report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
