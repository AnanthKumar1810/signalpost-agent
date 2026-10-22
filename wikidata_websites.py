"""Exact-identifier website discovery through Wikidata's public SPARQL endpoint."""
from __future__ import annotations

import json
import email.utils
import datetime as dt
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from request_budget import consume_request


ENDPOINT = "https://query.wikidata.org/sparql"
UA = "SignalpostAgent/0.4 (https://github.com/AnanthKumar1810/signalpost-agent; public company website discovery)"
BATCH_SIZE = 50
MAX_RESPONSE_BYTES = 2_000_000
BLOCKED_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "youtube.com", "tiktok.com", "proff.no", "purehelp.no", "1881.no", "gulesider.no",
}


def normalize_site_url(value: Any) -> str | None:
    """Accept only public-web-shaped HTTPS URL candidates from P856."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 80, 443):
            return None
    except ValueError:
        return None
    if any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS):
        return None
    # The existing page collector enforces HTTPS and performs the DNS/IP and
    # redirect checks before fetching. Upgrade a plain HTTP candidate in place.
    path = parsed.path or "/"
    netloc = f"[{host}]" if ":" in host else host
    return urllib.parse.urlunsplit(("https", netloc, path, "", ""))


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname or new.port not in (None, 443):
            raise ValueError("Wikidata SPARQL redirect escaped its fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WikidataWebsiteDiscovery:
    """Find P856 candidates only when Wikidata P2333 exactly matches the input org."""

    def __init__(self, timeout: float = 8, batch_size: int = BATCH_SIZE):
        self.timeout = timeout
        self.batch_size = max(1, min(int(batch_size), 50))
        self.requests = 0
        self.errors: list[str] = []
        self.failed_organisations: set[str] = set()
        self.deferred_organisations: set[str] = set()
        self.retry_count = 0

    @staticmethod
    def _has_verified_website(profile: dict[str, Any]) -> bool:
        return any(claim.get("family") == "web_presence" and claim.get("status") == "available"
                   and any(fact.get("field") == "website_url" and fact.get("value")
                           for fact in claim.get("facts", []))
                   for claim in profile.get("claims", []) if isinstance(claim, dict))

    def _query(self, orgs: list[str]) -> tuple[bytes, list[dict[str, str]]]:
        values = " ".join('"' + org + '"' for org in orgs)
        query = (
            "SELECT ?org ?item ?site WHERE { VALUES ?org { " + values + " } "
            "?item wdt:P2333 ?org; wdt:P856 ?site. } LIMIT " + str(len(orgs) * 4)
        )
        params = urllib.parse.urlencode({"query": query, "format": "json"})
        request = urllib.request.Request(
            ENDPOINT + "?" + params,
            headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
        )
        opener = urllib.request.build_opener(_SameHostRedirect())
        consume_request("Wikidata SPARQL")
        with opener.open(request, timeout=self.timeout) as response:
            if response.geturl().split("/", 3)[:3] != ["https:", "", "query.wikidata.org"]:
                raise ValueError("Wikidata returned a response from an unexpected host")
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Wikidata response exceeded the 2 MB safety limit")
        payload = json.loads(body)
        bindings = payload.get("results", {}).get("bindings", [])
        rows: list[dict[str, str]] = []
        wanted = set(orgs)
        for binding in bindings:
            org = str((binding.get("org") or {}).get("value", ""))
            item = str((binding.get("item") or {}).get("value", ""))
            raw_site = (binding.get("site") or {}).get("value", "")
            site = normalize_site_url(raw_site)
            item_url = urllib.parse.urlsplit(item)
            qid = item_url.path.rsplit("/", 1)[-1]
            if (org in wanted and item_url.scheme in {"http", "https"}
                    and item_url.hostname in {"wikidata.org", "www.wikidata.org"}
                    and re.fullmatch(r"Q[1-9][0-9]*", qid) and site):
                rows.append({"organisation_number": org, "item": item, "qid": qid, "site": site})
        return body, rows

    def discover(self, profiles: list[dict[str, Any]], deadline: float | None = None) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        targets = {
            str(row.get("organisation_number")): row
            for row in profiles
            if (row.get("identity_anchor") or {}).get("status") == "verified"
            and not self._has_verified_website(row)
        }
        candidates: dict[str, dict[str, str]] = {}
        if not targets:
            return candidates, {"status": "no_targets", "requests": 0, "target_count": 0, "candidate_count": 0, "errors": []}
        orgs = sorted(org for org in targets if re.fullmatch(r"[0-9]{9}", org))
        deadline_reached = False
        for offset in range(0, len(orgs), self.batch_size):
            if deadline is not None and time.monotonic() >= deadline:
                deadline_reached = True
                break
            batch = orgs[offset:offset + self.batch_size]
            attempt = 0
            while True:
                self.requests += 1
                try:
                    _, rows = self._query(batch)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or attempt >= 1:
                        self.errors.append(f"batch {offset // self.batch_size + 1}: {type(exc).__name__}: {str(exc)[:200]}")
                        self.failed_organisations.update(batch)
                        rows = []
                        break
                    retry_after = (exc.headers or {}).get("Retry-After", "")
                    wait_seconds: float | None = None
                    try:
                        wait_seconds = max(0.0, float(retry_after))
                    except (TypeError, ValueError):
                        try:
                            retry_at = email.utils.parsedate_to_datetime(str(retry_after))
                            if retry_at.tzinfo is None:
                                retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
                            wait_seconds = max(0.0, (retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            wait_seconds = 1.0
                    remaining = (deadline - time.monotonic()) if deadline is not None else 5.0
                    if wait_seconds > min(5.0, max(0.0, remaining)):
                        self.errors.append(f"batch {offset // self.batch_size + 1}: HTTP 429 retry deferred by Retry-After ({retry_after or 'unspecified'})")
                        self.deferred_organisations.update(batch)
                        rows = []
                        break
                    time.sleep(wait_seconds)
                    attempt += 1
                    self.retry_count += 1
                except Exception as exc:
                    self.errors.append(f"batch {offset // self.batch_size + 1}: {type(exc).__name__}: {str(exc)[:200]}")
                    self.failed_organisations.update(batch)
                    rows = []
                    break
            for row in rows:
                # Stable preference: HTTPS, then normalized hostname and URL.
                current = candidates.get(row["organisation_number"])
                if current is None or (row["site"].lower(), row["item"]) < (current["site"].lower(), current["item"]):
                    candidates[row["organisation_number"]] = row
        status = "partial" if deadline_reached or self.deferred_organisations else "complete" if not self.errors else "partial" if candidates else "failed"
        return candidates, {
            "status": status,
            "requests": self.requests,
            "target_count": len(orgs),
            "candidate_count": len(candidates),
            "failed_organisations": sorted(self.failed_organisations),
            "deferred_organisations": sorted(self.deferred_organisations),
            "retry_count": self.retry_count,
            "deadline_reached": deadline_reached,
            "errors": self.errors,
            "identity_rule": "Wikidata P2333 exactly equals requested organisation number; P856 is only a candidate, and fetched page still needs independent identity verification.",
            "license": "Wikidata structured data (CC0)",
        }
