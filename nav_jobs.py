"""Bounded client for NAV's official public job-vacancy feed."""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from collections import defaultdict
from typing import Any
from request_budget import consume_request

BASE = "https://pam-stilling-feed.nav.no"
UA = "SignalpostResearchAgent/0.3 (public NAV job feed)"


class _NavRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        target = urllib.parse.urlparse(newurl)
        if target.scheme != "https" or target.hostname != urllib.parse.urlparse(BASE).hostname or target.port not in (None, 443):
            raise ValueError("NAV redirect escaped the fixed HTTPS host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _name_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    tokens = re.findall(r"[a-z0-9]+", value)
    while tokens and tokens[-1] in {"as", "asa", "nuf", "da", "ans", "sa", "ba", "enk"}:
        tokens.pop()
    return " ".join(tokens)


def _name_is_candidate(feed_name: Any, legal_key: str) -> bool:
    """Loose prefilter to fetch candidate detail only; never publishes a job.

    Final attribution still requires the exact organisation number in the NAV
    detail, or the existing exact-name plus location/homepage corroboration.
    """
    candidate = _name_key(feed_name)
    if not candidate or not legal_key:
        return False
    if candidate == legal_key:
        return True
    target_tokens, candidate_tokens = set(legal_key.split()), set(candidate.split())
    shared = target_tokens & candidate_tokens
    distinctive = {token for token in target_tokens if len(token) >= 5}
    return bool(shared & distinctive) and len(shared) / max(1, len(target_tokens)) >= 0.5


class NavJobFeed:
    """Scan recent NAV feed pages once per batch and verify candidate ad details."""

    def __init__(self, timeout: float = 8, max_pages: int = 25, max_details: int = 100, lookback_days: int = 180):
        self.timeout = timeout
        self.max_pages = max(1, min(int(max_pages), 100))
        self.max_details = max(1, min(int(max_details), 500))
        self.lookback_days = max(1, min(int(lookback_days), 183))
        self.requests = 0
        self.error: str | None = None
        self.truncated = False

    def _get(self, url: str, token: str | None = None, modified_since: str | None = None) -> tuple[bytes, Any]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != urllib.parse.urlparse(BASE).hostname or parsed.port not in (None, 443):
            raise ValueError("NAV feed URL escaped its fixed HTTPS host")
        headers = {"User-Agent": UA, "Accept": "application/json, text/plain"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if modified_since:
            headers["If-Modified-Since"] = modified_since
        req = urllib.request.Request(url, headers=headers)
        self.requests += 1
        consume_request("NAV public feed")
        opener = urllib.request.build_opener(_NavRedirect())
        with opener.open(req, timeout=self.timeout) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("NAV feed response exceeds 2 MB")
        if "/api/publicToken" in url:
            return raw, None
        return raw, json.loads(raw.decode("utf-8"))

    @staticmethod
    def _registry_values(profile: dict[str, Any]) -> tuple[str, str, str]:
        name = str((profile.get("identity_anchor") or {}).get("legal_name") or "")
        website = ""
        municipality = ""
        for claim in profile.get("claims", []):
            if claim.get("family") != "company_details":
                continue
            for fact in claim.get("facts", []):
                if fact.get("field") == "website":
                    website = str(fact.get("value") or "")
                if fact.get("field") == "municipality":
                    municipality = str(fact.get("value") or "")
        return name, website, municipality

    @staticmethod
    def _host(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        parsed = urllib.parse.urlparse(value if "://" in value else "https://" + value)
        return (parsed.hostname or "").lower().removeprefix("www.")

    def enrich(self, profiles: list[dict[str, Any]]) -> dict[str, Any]:
        targets: dict[str, dict[str, Any]] = {}
        by_name: dict[str, list[str]] = defaultdict(list)
        for profile in profiles:
            org = str(profile.get("organisation_number") or "")
            if (profile.get("identity_anchor") or {}).get("status") != "verified":
                continue
            name, website, municipality = self._registry_values(profile)
            key = _name_key(name)
            if org and key:
                targets[org] = {"profile": profile, "name": name, "key": key, "host": self._host(website), "municipality": _name_key(municipality)}
                by_name[key].append(org)
        if not targets:
            return {"requests": 0, "matched_profiles": 0, "status": "no_verified_targets"}

        try:
            token_body, _ = self._get(BASE + "/api/publicToken")
            token_lines = [line.strip() for line in token_body.decode("utf-8", "replace").splitlines() if line.strip()]
            token = token_lines[-1] if token_lines else ""
            if not token or token.count(".") != 2:
                raise ValueError("NAV did not return a usable public experiment token")
            # NAV says postings cannot remain active longer than six months.
            # Cover that entire active lifetime (within a bounded page budget)
            # so a posting that has not changed this week is not systematically
            # invisible to the collector.
            since = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=self.lookback_days), usegmt=True)
            feed_url = BASE + "/api/v1/feed"
            candidates: dict[str, dict[str, Any]] = {}
            active_feed_entries = 0
            for _ in range(self.max_pages):
                _raw, page = self._get(feed_url, token, since)
                if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                    raise ValueError("NAV returned an invalid feed page")
                for item in page["items"]:
                    if not isinstance(item, dict):
                        continue
                    header = item.get("_feed_entry") or {}
                    if header.get("status") != "ACTIVE":
                        continue
                    active_feed_entries += 1
                    key = _name_key(header.get("businessName"))
                    # Exact names are cheapest. Similar names are fetched only
                    # as bounded candidates because the detail must then prove
                    # the exact org number before it is published.
                    possible_orgs = list(by_name.get(key, []))
                    if not possible_orgs:
                        possible_orgs = [org for org, target in targets.items()
                                         if _name_is_candidate(key, target["key"])]
                    for org in possible_orgs:
                        candidates.setdefault(str(header.get("uuid") or item.get("id") or ""), {"org": org, "url": item.get("url"), "header": header})
                next_url = page.get("next_url")
                if not next_url:
                    feed_url = ""
                    break
                next_full = urllib.parse.urljoin(BASE, str(next_url))
                if not next_full.startswith(BASE + "/api/v1/feed/"):
                    raise ValueError("NAV returned an unsafe pagination URL")
                feed_url = next_full
            else:
                self.truncated = True

            by_org: dict[str, list[dict[str, Any]]] = defaultdict(list)
            detail_count = 0
            entity_mismatches = 0
            expired_or_undated = 0
            for candidate in candidates.values():
                if detail_count >= self.max_details:
                    self.truncated = True
                    break
                relative = candidate.get("url")
                detail_url = urllib.parse.urljoin(BASE, str(relative or ""))
                if not detail_url.startswith(BASE + "/api/v1/feedentry/"):
                    continue
                try:
                    _raw, detail = self._get(detail_url, token)
                except Exception:
                    continue
                detail_count += 1
                if not isinstance(detail, dict) or detail.get("status") != "ACTIVE":
                    continue
                ad = detail.get("ad_content") or detail.get("json") or {}
                if not isinstance(ad, dict):
                    continue
                org = candidate["org"]
                target = targets[org]
                employer = ad.get("employer") if isinstance(ad.get("employer"), dict) else {}
                employer_org = re.sub(r"\D", "", str(employer.get("orgnr") or ""))
                employer_name_matches = _name_key(employer.get("name") or candidate["header"].get("businessName")) == target["key"]
                work_locations = ad.get("workLocations") if isinstance(ad.get("workLocations"), list) else []
                location_matches = any(_name_key(loc.get("municipal")) == target["municipality"] for loc in work_locations if isinstance(loc, dict))
                homepage_matches = bool(target["host"] and self._host(employer.get("homepage")) == target["host"])
                if employer_org:
                    if employer_org != org:
                        entity_mismatches += 1
                        continue
                elif not (employer_name_matches and (location_matches or homepage_matches)):
                    entity_mismatches += 1
                    continue
                expires = ad.get("expires")
                try:
                    expiry = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=dt.timezone.utc)
                    if expiry <= dt.datetime.now(dt.timezone.utc):
                        expired_or_undated += 1
                        continue
                except (TypeError, ValueError):
                    expired_or_undated += 1
                    continue
                title = ad.get("jobtitle") or ad.get("title") or candidate["header"].get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                application_url = ad.get("applicationUrl")
                if isinstance(application_url, str):
                    parsed_apply = urllib.parse.urlparse(application_url)
                    if parsed_apply.scheme != "https" or not parsed_apply.hostname:
                        application_url = None
                value = {"title": title.strip(), "published_at": ad.get("published"), "expires_at": expires,
                         "work_locations": [{k: loc.get(k) for k in ("city", "municipal", "county") if loc.get(k)}
                                            for loc in work_locations if isinstance(loc, dict)],
                         "application_url": application_url}
                by_org[org].append({"url": detail_url, "value": value, "excerpt": f"{title.strip()} — employer={employer.get('name')}; status=ACTIVE; expires={expires}"[:500]})

            matched = 0
            for org, target in targets.items():
                profile = target["profile"]
                existing = next((c for c in profile.get("claims", []) if c.get("family") == "jobs"), None)
                existing_facts = list(existing.get("facts", [])) if existing else []
                facts = existing_facts[:]
                for ad in by_org.get(org, []):
                    snapshot = {"id": "", "url": ad["url"]}
                    # The caller replaces this placeholder with a stateful content snapshot.
                    facts.append({"field": "active_job", "value": ad["value"], "_nav_url": ad["url"], "_nav_excerpt": ad["excerpt"]})
                if len(facts) > len(existing_facts):
                    matched += 1
                    if existing:
                        existing["facts"] = facts
                        existing["status"] = "available"
                        existing.pop("reason", None)
                    else:
                        profile.setdefault("claims", []).append({"family": "jobs", "status": "available", "facts": facts})
            return {"requests": self.requests, "matched_profiles": matched, "status": "complete", "truncated": self.truncated,
                    "pages_limit": self.max_pages, "detail_limit": self.max_details, "lookback_days": self.lookback_days,
                    "active_feed_entries_seen": active_feed_entries, "candidate_details_queued": len(candidates),
                    "details_fetched": detail_count, "entity_mismatches": entity_mismatches,
                    "expired_or_undated_details": expired_or_undated}
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return {"requests": self.requests, "matched_profiles": 0, "status": "failed", "error": self.error}
