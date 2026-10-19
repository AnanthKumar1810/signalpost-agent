"""Brave Search candidate discovery for verified Norwegian company websites.

Search results are transient leads only. This module does not return snippets,
titles or raw response bodies; every returned URL must still pass Agent.website.
The operator must use a Brave plan whose terms allow any result-derived data
the application retains (including a verified source URL) to be stored.
"""
from __future__ import annotations

import json
import ipaddress
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from wikidata_websites import normalize_site_url

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
API_HOST = "api.search.brave.com"
MAX_RESPONSE_BYTES = 2_000_000
REQUEST_ESTIMATE_USD = 0.005  # Brave Search's published $5 / 1,000 request list price.


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != API_HOST or new.port not in (None, 443):
            raise ValueError("Brave Search API redirect escaped its fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class BraveWebsiteDiscovery:
    """Find public-site candidates by exact org number, then exact legal name."""

    def __init__(self, api_key: str | None = None, timeout: float = 8,
                 max_requests: int = 600, max_candidates: int = 3):
        self.api_key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY")
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is required to enable Brave discovery")
        if max_requests < 1 or max_requests > 2000:
            raise ValueError("Brave max_requests must be between 1 and 2000")
        self.timeout = timeout
        self.max_requests = max_requests
        self.max_candidates = max(1, min(int(max_candidates), 5))
        self.requests = 0
        self.billable_requests = 0
        self.errors: list[str] = []

    @staticmethod
    def _parse_candidates(body: bytes, limit: int) -> list[str]:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Brave Search response exceeded the 2 MB safety limit")
        payload = json.loads(body)
        results = payload.get("web", {}).get("results", [])
        if not isinstance(results, list):
            return []
        urls: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            # Only the page URL is used transiently. Search titles/snippets are
            # intentionally discarded and never treated as evidence.
            candidate = normalize_site_url(item.get("url", ""))
            if candidate:
                host = urllib.parse.urlsplit(candidate).hostname or ""
                try:
                    if not ipaddress.ip_address(host).is_global:
                        candidate = None
                except ValueError:
                    if host.lower() in {"localhost", "localhost.localdomain"}:
                        candidate = None
            if candidate and candidate not in urls:
                urls.append(candidate)
            if len(urls) >= limit:
                break
        return urls

    def _search(self, query: str) -> list[str]:
        if self.requests >= self.max_requests:
            return []
        params = urllib.parse.urlencode({
            "q": query,
            "count": 10,
            "country": "NO",
            "search_lang": "no",
        })
        request = urllib.request.Request(
            ENDPOINT + "?" + params,
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key,
                     "User-Agent": "SignalpostResearchAgent/0.2"},
        )
        self.requests += 1
        opener = urllib.request.build_opener(_SameHostRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname != API_HOST:
                    raise ValueError("Brave Search response came from an unexpected host")
                body = response.read(MAX_RESPONSE_BYTES + 1)
            self.billable_requests += 1
            return self._parse_candidates(body, self.max_candidates)
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            return []

    def discover(self, organisation_number: str, legal_name: str) -> dict[str, Any]:
        org = re.sub(r"[\s-]", "", str(organisation_number))
        if not re.fullmatch(r"[0-9]{9}", org):
            return {"status": "not_applicable", "candidate_urls": [], "requests": 0,
                    "estimated_cost_usd": 0.0, "reason": "Invalid Norwegian organisation number"}
        starting_requests = self.requests
        starting_errors = len(self.errors)
        starting_billable = self.billable_requests
        if self.requests >= self.max_requests:
            return {"status": "budget_exhausted", "candidate_urls": [], "requests": 0,
                    "estimated_cost_usd": 0.0, "reason": "Brave request budget is exhausted"}
        # The organisation number is the primary query: it is a unique key and
        # is required on Norwegian company websites. Use the legal name only as
        # a bounded fallback when number search returns no usable candidate.
        candidates = self._search(f'"{org}"')
        if not candidates and len(self.errors) == starting_errors and legal_name.strip() and self.requests < self.max_requests:
            candidates = self._search(f'"{legal_name.strip()}"')
        used = self.requests - starting_requests
        billable = self.billable_requests - starting_billable
        status = "candidate_found" if candidates else "failed" if len(self.errors) > starting_errors else "not_found"
        return {
            "status": status,
            "candidate_urls": candidates,
            "requests": used,
            "billable_requests": billable,
            "estimated_cost_usd": round(billable * REQUEST_ESTIMATE_USD, 6),
            "reason": "Search results are candidate leads only; fetched pages still require independent company identity verification.",
        }
