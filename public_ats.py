"""Small public job-board adapters used only for ATS links advertised by a verified company site."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Callable


_BOARD_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


def linked_board(value: str) -> dict[str, str] | None:
    """Return a supported board only for an explicit HTTPS link on the company page."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or not _BOARD_SLUG.fullmatch(parts[0]):
        return None
    slug = parts[0]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return {"provider": "greenhouse", "slug": slug, "link": value}
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return {"provider": "lever_eu" if host == "jobs.eu.lever.co" else "lever", "slug": slug, "link": value}
    if host == "jobs.ashbyhq.com":
        return {"provider": "ashby", "slug": slug, "link": value}
    return None


def endpoint(board: dict[str, str]) -> tuple[str, str]:
    slug = urllib.parse.quote(board["slug"], safe="")
    provider = board["provider"]
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", "boards-api.greenhouse.io"
    if provider in {"lever", "lever_eu"}:
        host = "api.eu.lever.co" if provider == "lever_eu" else "api.lever.co"
        return f"https://{host}/v0/postings/{slug}?mode=json&limit=100", host
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}", "api.ashbyhq.com"


def _job_url(value: Any, hosts: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in hosts or parsed.username or parsed.password:
            return None
        return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))
    except ValueError:
        return None


def normalize_jobs(board: dict[str, str], payload: Any, board_url: str) -> list[dict[str, Any]]:
    provider = board["provider"]
    rows = payload.get("jobs", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    results: list[dict[str, Any]] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        if provider == "greenhouse":
            title, location, url = row.get("title"), (row.get("location") or {}).get("name") if isinstance(row.get("location"), dict) else None, row.get("absolute_url")
            published = None
            source_updated = row.get("updated_at")
            allowed_hosts = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
        elif provider.startswith("lever"):
            cats = row.get("categories") if isinstance(row.get("categories"), dict) else {}
            title, location, url = row.get("text"), cats.get("location"), row.get("hostedUrl")
            published, source_updated = row.get("createdAt"), None
            allowed_hosts = {"jobs.lever.co", "jobs.eu.lever.co"}
        else:
            title, location, url = row.get("title"), row.get("location"), row.get("jobUrl")
            published, source_updated = row.get("publishedAt"), None
            allowed_hosts = {"jobs.ashbyhq.com"}
        safe_url = _job_url(url, allowed_hosts)
        if not isinstance(title, str) or not title.strip() or not safe_url:
            continue
        results.append({"title": title.strip()[:300], "location": str(location).strip()[:200] if location else None,
                        "url": safe_url, "published_at": published if isinstance(published, str) else None,
                        "source_updated_at": source_updated if isinstance(source_updated, str) else None,
                        "provider": provider, "board_url": board_url})
    return results


def fetch_linked_jobs(links: list[str], fetch_json: Callable[[str], tuple[Any, bytes, int]], max_boards: int = 3,
                      max_jobs_per_board: int = 30) -> dict[str, Any]:
    """Fetch only bounded public listings linked from the verified first-party homepage."""
    boards: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        board = linked_board(link)
        if board and (board["provider"], board["slug"]) not in seen:
            seen.add((board["provider"], board["slug"]))
            boards.append(board)
            if len(boards) >= max_boards:
                break
    jobs: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    requests = 0
    for board in boards:
        url, _api_host = endpoint(board)
        try:
            payload, raw, _status = fetch_json(url)
            requests += 1
            api_jobs = normalize_jobs(board, payload, board["link"])
            responses.append({"board": board, "url": url, "raw": raw, "jobs": api_jobs})
            jobs.extend(api_jobs[:max_jobs_per_board])
        except Exception as exc:
            # Count attempted outbound requests, including failed attempts/retries
            # reported by the caller's request counter.
            requests += 1
            errors.append({"provider": board["provider"], "message": f"{type(exc).__name__}: {str(exc)[:180]}"})
    return {"jobs": jobs, "responses": responses, "errors": errors, "requests": requests,
            "boards_checked": len(boards), "boards_discovered": len(seen)}
