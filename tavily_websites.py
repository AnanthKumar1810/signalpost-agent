"""Tavily Search candidate discovery for verified Norwegian company websites.

Search result text is never evidence. Only normalized candidate URLs leave this
module; the main agent must fetch each page and independently verify the entity.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from tinyfish_websites import _domain_matches_name, _is_directory
from wikidata_websites import normalize_site_url
from request_budget import consume_request

ENDPOINT = "https://api.tavily.com/search"
API_HOST = "api.tavily.com"
MAX_RESPONSE_BYTES = 2_000_000


class _FixedHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != API_HOST or target.port not in (None, 443):
            raise ValueError("Tavily Search redirect escaped its fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_public_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


class TavilyWebsiteDiscovery:
    """Find candidate websites by Norwegian organisation number, then legal name."""

    def __init__(self, api_key: str | None = None, timeout: float = 10,
                 max_requests: int = 600, max_candidates: int = 3):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY is required to enable Tavily discovery")
        if max_requests < 1 or max_requests > 2000:
            raise ValueError("Tavily max_requests must be between 1 and 2000")
        self.timeout = timeout
        self.max_requests = max_requests
        self.max_candidates = max(1, min(int(max_candidates), 5))
        self.requests = 0
        self.credits_used = 0
        self.errors: list[str] = []

    @staticmethod
    def _parse_candidates(body: bytes, limit: int) -> tuple[list[str], int]:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Tavily Search response exceeded the 2 MB safety limit")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return [], 0
        results = payload.get("results", [])
        if not isinstance(results, list):
            return [], 0
        urls: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            candidate = normalize_site_url(item.get("url", ""))
            if candidate:
                host = urllib.parse.urlsplit(candidate).hostname or ""
                if _is_directory(host) or not _is_public_host(host):
                    candidate = None
            if candidate and candidate not in urls:
                urls.append(candidate)
            if len(urls) >= limit:
                break
        usage = payload.get("usage")
        credits = usage.get("credits", 0) if isinstance(usage, dict) else 0
        try:
            credits = max(0, int(credits))
        except (TypeError, ValueError):
            credits = 0
        return urls, credits

    def _search(self, query: str) -> list[str]:
        if self.requests >= self.max_requests:
            return []
        body = json.dumps({
            "query": query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": 10,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_usage": True,
            "country": "Norway",
            "language": "no",
        }).encode("utf-8")
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     "User-Agent": "SignalpostResearchAgent/0.4"},
            method="POST",
        )
        self.requests += 1
        try:
            consume_request("Tavily Search")
            with urllib.request.build_opener(_FixedHostRedirect()).open(request, timeout=self.timeout) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname != API_HOST:
                    raise ValueError("Tavily Search response came from an unexpected host")
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
            urls, credits = self._parse_candidates(response_body, self.max_candidates)
            self.credits_used += credits
            return urls
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            return []

    def discover(self, organisation_number: str, legal_name: str) -> dict[str, Any]:
        org = re.sub(r"[\s-]", "", str(organisation_number))
        if not re.fullmatch(r"[0-9]{9}", org):
            return {"status": "not_applicable", "candidate_urls": [], "requests": 0,
                    "credits_used": 0, "reason": "Invalid Norwegian organisation number"}
        start_requests, start_errors, start_credits = self.requests, len(self.errors), self.credits_used
        if self.requests >= self.max_requests:
            return {"status": "budget_exhausted", "candidate_urls": [], "requests": 0,
                    "credits_used": 0, "reason": "Tavily request budget is exhausted"}
        candidates = self._search(f'"{org}"')
        if (not candidates and len(self.errors) == start_errors and legal_name.strip()
                and self.requests < self.max_requests):
            candidates = self._search(f'"{legal_name.strip()}"')
        candidates.sort(key=lambda url: (not _domain_matches_name(url, legal_name), url))
        return {
            "status": "candidate_found" if candidates else "failed" if len(self.errors) > start_errors else "not_found",
            "candidate_urls": candidates,
            "requests": self.requests - start_requests,
            "credits_used": self.credits_used - start_credits,
            "estimated_cost_usd": 0.0,
            "reason": "Search results are candidate leads only; fetched pages still require independent company identity verification.",
        }
