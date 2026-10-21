"""Optional web-search claim discovery with deterministic evidence gates.

The model is a candidate finder, never the authority for identity or numeric facts.
Requires OPENAI_API_KEY; uses Responses API hosted web search. No paid calls occur
unless explicitly enabled by constructing WebSearchJudge and invoking discover().
"""
from __future__ import annotations

import json
import math
import os
import re
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any
from request_budget import consume_request
from urllib.robotparser import RobotFileParser

API = "https://api.openai.com/v1/responses"


def _output_text(response: dict[str, Any]) -> str:
    for item in response.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""


class WebSearchJudge:
    """Search for a few current non-financial claims; reject weak/mismatched claims."""
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = 35, max_calls: int = 2):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required to enable web-search judge")
        if max_calls < 1 or max_calls > 3:
            raise ValueError("max_calls must be between 1 and 3")
        self.model = model or os.environ.get("SIGNALPOST_JUDGE_MODEL", "gpt-4.1-mini")
        self.timeout, self.remaining = timeout, max_calls

    def discover(self, org: str, legal_name: str, query: str = "") -> dict[str, Any]:
        if self.remaining <= 0:
            return {"status": "budget_exhausted", "facts": [], "rejected": [], "calls_made": 0}
        self.remaining -= 1
        prompt = (
            "Find at most 5 recent public facts about the exact Norwegian legal entity below. "
            "Prioritize official company pages, Norwegian public sources, and dated job/activity pages. "
            "Do not return financial numbers or personal data. Search only; do not infer. "
            "For each candidate return JSON {facts:[{field,value,source_url,quote,as_of,entity_org_number,entity_name}]}. "
            "If the page does not establish the requested entity, omit the fact. "
            f"Exact organisation number: {org}; legal name: {legal_name}; topic: {query or 'current jobs and public activity'}."
        )
        body = json.dumps({"model": self.model, "tools": [{"type": "web_search_preview", "search_context_size": "low"}],
                           "input": prompt, "max_output_tokens": 1100}).encode()
        req = urllib.request.Request(API, data=body, headers={"Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json"}, method="POST")
        try:
            consume_request("OpenAI search judge")
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ValueError("response exceeds 1 MB safety limit")
                data = json.loads(raw)
        except Exception as exc:
            return {"status": "failed", "facts": [], "rejected": [], "error": type(exc).__name__, "calls_made": 1}
        try:
            parsed = json.loads(_output_text(data))
            raw_facts = parsed.get("facts", [])
        except Exception:
            return {"status": "failed", "facts": [], "rejected": ["model_output_not_json"], "calls_made": 1}
        if not isinstance(raw_facts, list):
            return {"status": "failed", "facts": [], "rejected": ["facts_not_array"], "calls_made": 1}
        accepted, rejected = [], []
        for fact in raw_facts[:10]:
            reason = self._reject_reason(fact, org, legal_name)
            if reason:
                rejected.append({"candidate": fact, "reason": reason})
            else:
                accepted.append({**fact, "observed_at": datetime.now(timezone.utc).isoformat(),
                                 "verification": "candidate_only_web_search_citation_requires_human_or_source_fetch_check"})
        return {"status": "available" if accepted else "not_available", "facts": accepted,
                "rejected": rejected, "model": self.model, "provider": "openai", "cost_usd": None, "calls_made": 1,
                "cost_note": "Usage is provider-billed; check account usage. Exact cost is not inferred here."}

    def verify_citations(self, result: dict[str, Any], org: str, legal_name: str) -> dict[str, Any]:
        """Fetch cited pages and require exact quote, entity and publication-date evidence.

        Search output is never upgraded based on the model's own assertion alone.
        Each page and robots.txt request is counted and bounded to three candidates.
        """
        from signalpost_agent import PageParser, open_safe, public_host
        facts = result.get("facts", []) if isinstance(result, dict) else []
        verified, rejected, requests = [], [], 0
        for fact in facts[:3]:
            reason = self._reject_reason(fact, org, legal_name)
            if reason:
                rejected.append({"candidate": fact, "reason": reason})
                continue
            parsed = urllib.parse.urlparse(fact["source_url"])
            host = parsed.hostname or ""
            try:
                if not public_host(host):
                    raise ValueError("source hostname did not resolve to a public address")
                robots_url = urllib.parse.urlunparse(("https", parsed.netloc, "/robots.txt", "", "", ""))
                robots_req = urllib.request.Request(robots_url, headers={"User-Agent": "SignalpostResearchAgent/0.2", "Accept": "text/plain"})
                requests += 1
                try:
                    with open_safe(robots_req, host, self.timeout) as response:
                        if urllib.parse.urlparse(response.geturl()).hostname != host:
                            raise ValueError("robots redirect changed host")
                        robots = response.read(100_001)
                        if len(robots) > 100_000:
                            raise ValueError("robots.txt exceeds size limit")
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        robots = b""
                    else:
                        raise
                rules = RobotFileParser(robots_url)
                rules.parse(robots.decode("utf-8", errors="replace").splitlines())
                if not rules.can_fetch("SignalpostResearchAgent/0.2", fact["source_url"]):
                    raise ValueError("robots.txt disallows the cited page")
                page_req = urllib.request.Request(fact["source_url"], headers={"User-Agent": "SignalpostResearchAgent/0.2", "Accept": "text/html"})
                requests += 1
                with open_safe(page_req, host, self.timeout) as response:
                    if urllib.parse.urlparse(response.geturl()).hostname != host:
                        raise ValueError("citation redirect changed host")
                    body = response.read(500_001)
                    if len(body) > 500_000:
                        raise ValueError("cited page exceeds size limit")
                    if response.headers.get_content_type() not in ("text/html", "application/xhtml+xml"):
                        raise ValueError("cited page is not HTML")
                parser = PageParser(fact["source_url"])
                parser.feed(body.decode("utf-8", errors="replace"))
                visible = " ".join(parser.text)
                normalized = " ".join(visible.split()).casefold()
                quote = " ".join(fact["quote"].split()).casefold()
                if quote not in normalized:
                    raise ValueError("cited quote was not found in fetched page text")
                exact_name = bool(legal_name and re.search(r"(?<![\w])" + re.escape(legal_name) + r"(?![\w])", visible, re.I))
                exact_org = bool(re.search(r"(?<![0-9])" + re.escape(org) + r"(?![0-9])", visible))
                if not (exact_name or exact_org):
                    raise ValueError("fetched page does not identify the exact company")
                source_dates = set()
                for value in parser.published_dates:
                    try:
                        source_dates.add(date.fromisoformat(value[:10]).isoformat())
                    except ValueError:
                        pass
                if fact["as_of"] not in source_dates:
                    raise ValueError("candidate date does not match publication date in fetched page")
                verified.append({**fact, "verification": "source_page_fetched_quote_entity_and_date_matched", "evidence_excerpt": quote[:600]})
            except Exception as exc:
                rejected.append({"candidate": fact, "reason": str(exc)[:240]})
        return {**result, "status": "verified" if verified else "not_available", "verified_facts": verified,
                "rejected": rejected, "verification_requests": requests}

    @staticmethod
    def _reject_reason(fact: Any, org: str, legal_name: str) -> str | None:
        if not isinstance(fact, dict): return "not_an_object"
        url, quote = fact.get("source_url", ""), fact.get("quote", "")
        if not isinstance(url, str) or not re.match(r"^https://[^\s]+$", url, re.I): return "source_not_https"
        try:
            parsed_url = urllib.parse.urlparse(url)
            if not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.port not in (None, 443):
                return "unsafe_source_url"
            try:
                if not ipaddress.ip_address(parsed_url.hostname).is_global:
                    return "unsafe_source_url"
            except ValueError:
                if parsed_url.hostname.lower() in {"localhost", "localhost.localdomain"}:
                    return "unsafe_source_url"
        except ValueError:
            return "unsafe_source_url"
        if not isinstance(quote, str) or len(quote.strip()) < 20: return "missing_substantive_quote"
        value = fact.get("value")
        if not isinstance(fact.get("field"), str) or not isinstance(value, (str, int, float, bool)): return "invalid_claim_shape"
        if isinstance(value, float) and not math.isfinite(value): return "invalid_claim_shape"
        cited_org = re.sub(r"[\s-]", "", str(fact.get("entity_org_number", "")))
        names = (str(fact.get("entity_name", "")).casefold(), legal_name.casefold())
        if cited_org and cited_org != org: return "entity_mismatch"
        if not cited_org and (not names[0] or names[0] != names[1]): return "entity_mismatch"
        as_of = fact.get("as_of")
        try:
            claim_date = date.fromisoformat(as_of) if isinstance(as_of, str) else None
        except ValueError:
            claim_date = None
        today = datetime.now(timezone.utc).date()
        if claim_date is None or (today - claim_date).days > 365 or claim_date > today:
            return "stale_or_invalid_date"
        return None
