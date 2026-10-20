"""TinyFish Search candidate discovery for verified Norwegian company websites.

Search-result text is never evidence. Only normalized candidate URLs leave this
module, and the main agent must fetch and independently verify the legal entity.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from typing import Any

from wikidata_websites import normalize_site_url

ENDPOINT = "https://api.search.tinyfish.ai"
API_HOST = "api.search.tinyfish.ai"
MAX_RESPONSE_BYTES = 2_000_000
DIRECTORY_HOSTS = {
    "brreg.no", "virksomhet.brreg.no", "proff.no", "purehelp.no", "1881.no",
    "gulesider.no", "kredittsjekk.no", "firmadatabasen.no", "sokfirma.no",
    "foretaksinfo.no", "foretak.io", "io.no", "yra.no", "vexter.no",
    "tracxn.com", "unrealfund.com", "21st.ai", "byndle.no", "utdanning.no",
    "yelp.com", "finansavisen.no", "eiendomssjekk.no", "rorleggerplassen.no",
    "forvalt.no", "1850.no", "bestillerorlegger.no",
}


class _FixedHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.hostname != API_HOST or target.port not in (None, 443):
            raise ValueError("TinyFish Search redirect escaped its fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_directory(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == blocked or host.endswith("." + blocked) for blocked in DIRECTORY_HOSTS)


def _domain_matches_name(url: str, legal_name: str) -> bool:
    def key(value: str) -> str:
        value = value.translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
        return "".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()))
    labels = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".").split(".")
    host_key = key(" ".join(label for label in labels[:-1]
                            if label not in {"www", "m", "no", "com", "co"}))
    words = [key(word) for word in legal_name.split()]
    words = [word for word in words if len(word) >= 4 and word not in {"asa", "ans", "enk", "nuf", "sam"}]
    return bool(host_key and words and (any(word in host_key for word in words) or "".join(words) in host_key))


class TinyFishWebsiteDiscovery:
    """Find candidate company sites by organisation number, then legal name."""

    def __init__(self, api_key: str | None = None, timeout: float = 10,
                 max_requests: int = 600, max_candidates: int = 3):
        self.api_key = api_key or os.environ.get("TINYFISH_API_KEY")
        if not self.api_key:
            raise ValueError("TINYFISH_API_KEY is required to enable TinyFish discovery")
        if max_requests < 1 or max_requests > 2000:
            raise ValueError("TinyFish max_requests must be between 1 and 2000")
        self.timeout = timeout
        self.max_requests = max_requests
        self.max_candidates = max(1, min(int(max_candidates), 5))
        self.requests = 0
        self.errors: list[str] = []

    @staticmethod
    def _parse_candidates(body: bytes, limit: int) -> list[str]:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("TinyFish Search response exceeded the 2 MB safety limit")
        payload = json.loads(body)
        results = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(results, list):
            return []
        urls: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            # Titles, snippets and other search-result fields are intentionally
            # discarded. A URL is only a lead for the independent page verifier.
            candidate = normalize_site_url(item.get("url", ""))
            if candidate:
                host = urllib.parse.urlsplit(candidate).hostname or ""
                if _is_directory(host):
                    candidate = None
                else:
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
        params = urllib.parse.urlencode({"query": query, "location": "Norway", "language": "no"})
        request = urllib.request.Request(
            ENDPOINT + "?" + params,
            headers={"Accept": "application/json", "X-API-Key": self.api_key,
                     "User-Agent": "SignalpostResearchAgent/0.3"},
        )
        self.requests += 1
        try:
            with urllib.request.build_opener(_FixedHostRedirect()).open(request, timeout=self.timeout) as response:
                if urllib.parse.urlsplit(response.geturl()).hostname != API_HOST:
                    raise ValueError("TinyFish Search response came from an unexpected host")
                body = response.read(MAX_RESPONSE_BYTES + 1)
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
        if self.requests >= self.max_requests:
            return {"status": "budget_exhausted", "candidate_urls": [], "requests": 0,
                    "estimated_cost_usd": 0.0, "reason": "TinyFish request budget is exhausted"}
        candidates = [url for url in self._search(f'"{org}"') if _domain_matches_name(url, legal_name)]
        if not candidates and len(self.errors) == starting_errors and legal_name.strip() and self.requests < self.max_requests:
            candidates = [url for url in self._search(f'"{legal_name.strip()}"') if _domain_matches_name(url, legal_name)]
        status = "candidate_found" if candidates else "failed" if len(self.errors) > starting_errors else "not_found"
        return {
            "status": status,
            "candidate_urls": candidates,
            "requests": self.requests - starting_requests,
            "estimated_cost_usd": 0.0,
            "reason": "Search results are candidate leads only; fetched pages still require independent company identity verification.",
            "pricing_note": "TinyFish Search was documented as free when this integration was implemented; verify current provider terms before a competition run.",
        }
