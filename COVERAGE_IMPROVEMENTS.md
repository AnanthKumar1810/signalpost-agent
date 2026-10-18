# Coverage improvement work — 18 October 2026

## Decision

A paid scraping API is not required for the current improvements. We tested NAV's official public job-vacancy feed and expanded first-party website activity discovery. The results show that these changes improve the collection method, but do not yet demonstrate a useful increase in the 100-company sample. In particular, the NAV path found **zero** matching companies in the bounded live experiment, so jobs coverage is not solved.

## Root-cause audit and follow-up implementation — 18 October 2026

The uploaded 2025 company universe contains 411,160 rows. Only 44,855 (10.91%) have a non-empty BRREG website field; 366,305 (89.09%) do not. Website registration is especially sparse in the dominant AS form (33,281/376,956; 8.8%) and in industry code 68 real-estate entities (4,411/90,187; 4.9%). This is the main measured reason website-backed jobs and activity coverage is low. The 100-company smoke sample had a lower 8/100 registered-site rate.

In the final saved smoke output, web presence was available for 1 company, unavailable for 92 because no BRREG site was listed, ambiguous for 4, and failed for 3 retrieval cases. Of the ambiguous results, three were fetched pages and one was an unsafe non-HTTPS URL. The three fetched pages were checked against their saved page-text snapshots. None contained the exact registered name (with or without legal-form suffix) or the requested organisation number. Therefore widening the name check alone would have rescued 0/3 in this batch. It would be wrong to claim that the strict heading check explains the 92 no-site outcomes.

Two bounded fixes are implemented:

- Legal-name verification now searches the whole fetched text for the complete distinctive name, optionally without its legal-form suffix. A short/single-token name is not accepted from body text alone; an exact organisation number remains the strongest proof. Formatted organisation numbers (for example `923 609 016`) are normalized before comparison. The original 100-company saved pages gain no verified sites from this change; fixtures cover cases the old matcher would miss and guard against partial and weak matches.
- `--wikidata-websites` adds a no-key, batched lookup for verified profiles without a BRREG-listed website. It selects P856 only from a Wikidata entity whose P2333 exactly equals the requested Norwegian organisation number. P856 is recorded as a candidate, never as a published company fact. The candidate page must still pass the existing HTTPS, public-address, robots, redirect, and page-identity checks before the agent publishes website facts or follows any same-host job/activity links. Directory and social-network domains are rejected. Each request and lookup failure is included in the run report; a failed discovery is not silently described as a successful empty search.

The full local test suite passes 78 tests, including four new Wikidata connector tests and the site-name/organisation-number cases. **No live coverage gain is claimed yet.** The test environment could not resolve `query.wikidata.org`, so the connector has not been measured on the supplied 100 companies. A live 100-company rerun with `--wikidata-websites`, followed by manual review of newly found sites and false-positive checks, is the next required measurement. The website discovery is included as an opt-in until that run succeeds.

The attached starter kit supports a broader lawful search-provider path, but its Brave connector requires a separately provisioned API key and explicit result-retention terms. No key was supplied, and its output cannot be measured here; it is not enabled or represented as a completed coverage improvement.

## Changes made

- Added an opt-in `--nav-jobs` batch lookup using NAV's public feed. It only processes profiles whose BRREG identity is verified, starts with recent changes, limits the scan to three pages and 100 job details, rechecks each ad's active status and expiry, and requires an exact employer organisation number or a strict company-name plus location/homepage match. It rejects cross-host redirects and counts shared requests.
- Added RSS and Atom discovery for feeds the company's BRREG-listed HTTPS website explicitly advertises. Feed requests stay on that same host, follow `robots.txt`, are size-limited, and require a dated item with a same-host article URL before activity can be published. A failed feed request is reported separately and does not erase facts from other sources.
- Added offline tests for feed discovery, dated activity evidence, NAV's employer verification and redirect protection.

## Measured results

The full offline suite passes **71/71 tests**. These tests establish behavior on controlled fixtures; they do not establish real-world feed coverage.

The existing live 100-company BRREG baseline contains 100 verified identities, 316 requests and a 103.5-second wall time. It recorded 1 available jobs result, 96 not available and 3 failed; public activity had 97 not available and 3 failed. This is the historical baseline from `outputs/live-100-hardened.jsonl.report.json`.

I ran the NAV client over those same 100 verified profiles, without repeating the BRREG lookups. At the three-page cap it made 4 requests (one public token plus three feed pages), found zero candidate companies, and reported truncation. I then increased the experimental page cap to 10: it made 11 requests, still found zero candidate companies, and again hit the cap. A separate `?last` feed probe returned one active posting, but it did not match any of the 100 target companies. These outcomes do not support claiming NAV improved this batch. The bounded default is retained because an uncapped historical feed scan was too slow to complete during the earlier attempt.

RSS/Atom behavior passed fixture tests and was then checked live. A one-company rerun for the site that advertised a feed returned 10 dated activity items, passed the final evidence-integrity gate, and had zero errors. The final integrated 100-company run returned 100 complete profiles in 108.7 seconds using 322 requests. It recorded 1 jobs profile, 96 jobs not available, and 3 jobs failed; public activity was available for 1 profile, not available for 96, and failed for 3. Compared with the earlier run (1 jobs available; 0 public-activity available, 97 not available, 3 failed), the activity collector added one available profile; NAV added no jobs matches. One finance lookup still returned HTTP 500. These are local measurements, not an official Builderr score.

## Why no general-purpose scraper was added

A scraper can return more pages, but it does not establish that a job or announcement belongs to the correct legal entity. This implementation first uses public sources tied to the registered company website and official NAV data. LinkedIn's own help page prohibits scraping software; Internshala is India-focused and is not an appropriate primary source for Norwegian vacancies. A third-party scraper should only be considered if it can access sources permitted by the challenge, identify its source URLs, fit the request/cost budget, and pass the same attribution and freshness gates.

## Next highest-value work

1. Add public ATS job-board connectors only when a company's own careers page links to that board. Greenhouse, Lever and Ashby publish public job endpoints, but any result must retain the company-site-to-board link as attribution and be verified for active status and source URL. Start with one provider and benchmark it against the held-out sample before adding more.
2. Add persistent NAV feed cursors for scheduled runs. NAV recommends consuming the feed incrementally; a short recent-window scan cannot provide complete coverage. A first complete backfill is not suitable for the current bounded batch command.
3. Inspect the saved `outputs/live-100-coverage-final.jsonl` examples, especially the 10 dated feed items, and rerun from a stable network before using these figures as a submission artifact. Continue measuring false positives as well as coverage gains before changing the score estimate.

Official source documentation: [NAV Job Vacancy Feed](https://navikt.github.io/pam-stilling-feed/), [Greenhouse Job Board API](https://docs.greenhouse.io/job-board), [Lever postings API](https://github.com/lever/postings-api), [Ashby public job-posting API](https://developers.ashbyhq.com/docs/public-job-posting-api), and [LinkedIn scraping policy](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibition-of-scraping-software?intendedLocale=en&lang=en-us).
