# Signalpost engineering completion report

**Prepared:** 17 October 2026  
**Project:** Signalpost company-intelligence agent, local workspace `signalpost-agent-v2`  
**Status:** Working local package; not submitted to Builderr. Follow-up diagnosis corrected the accounts host to BRREG production. The host could not be live-verified from this run because DNS resolution was unavailable, so financial coverage remains unconfirmed.

## 1. What the project is meant to do

The program takes Norwegian organisation numbers and produces one profile envelope for every input. It should anchor each company to the exact legal entity, collect useful public information, show where every fact came from, preserve source history, and say clearly when a source is empty, blocked, ambiguous or broken.

Builderr's current page asks for the runnable agent and a 100-company smoke-test result or report. It says the official company batch is supplied later and precomputed profiles are not used for ranking. A local 1,000-company test is allowed, but 1,000 saved profiles are not required for entry. See [Builderr's submission and scoring rules](https://builderr.ai/challenges/signalpost).

## 2. Architecture decisions

The pipeline uses the organisation number as the first identity key. It validates the Norwegian checksum and normalizes display spaces and hyphens. It then reads BRREG's entity record, uses the returned legal name and registration data as the anchor, and obtains registered roles. It attempts a separate accounts lookup only for that exact number. A company website is considered only when BRREG lists it. Website retrieval checks robots policy, HTTPS, public network addresses, response size and same-host redirects. The page must support the exact legal name or organisation number before page claims are published.

Each fact points to an evidence record and a source snapshot. Snapshots contain the URL, retrieval time and content hash. SQLite keeps the prior version; identical content does not create a change, and changed content is reported with the prior hash and changed fields. The batch runner uses four bounded worker threads by default and gives each company its own SQLite connection. Output is written in input order. Missing family results are converted into explicit failures, and one company's exception cannot silently drop the rest of the batch.

A deterministic summary uses the verified registry facts. It does not invent a description when identity is unverified. An HTML page provides a local way to search and inspect results; untrusted text is escaped. The optional `claim_judge.py` uses OpenAI-hosted web search only when explicitly enabled with a key. It returns candidates as unverified; it is not enabled in the default command, its candidate output does not become factual claims, and no paid model or search call occurred in testing.

## 3. What was learned from the reference systems

| Reference | Useful architecture | Project decision | Practical test status |
|---|---|---|---|
| Fire Enrich | Separate enrichment modules and source attribution | Adapted into claim families and evidence IDs. Rejected website/domain-first identification as the primary company key. | Architecture reviewed; upstream application not launched or benchmarked. |
| Company Research Agent | Plan, collect, curate, summarize in separate stages | Adapted as a small deterministic identify → collect → curate → summarize pipeline. Did not run a full agent graph per company. | Architecture reviewed; upstream application not launched or benchmarked. |
| FORGE / Dataforge | Durable checkpoints, recovery and careful database updates | Adapted as per-company error containment, content-addressed snapshots and explicit change detection. | Architecture reviewed; upstream application not launched or benchmarked. |
| python-brreg | Norway-specific organisation-number lookup and typed registry access | Adapted as BRREG entity and roles requests anchored by the nine-digit legal number. | Project code tested with fixtures and real BRREG lookups. The later seeded run verified 100/100 identities. |
| Crawl4AI | Bounded crawling and structured extraction | Used a smaller standard-library crawler with robots and URL safety gates. It avoids a browser dependency but misses JavaScript-only pages. | Project crawler tested with fixtures and real registered sites; one site and job were published in the 100-company run. |

These comparisons are design reviews, not a claim that five third-party frameworks were installed and timed side by side. Their credentials, runtime and dependency environments were not set up in this task. The useful parts were selected for the local implementation; a fair benchmark of the upstream projects remains future work.

## 4. Sources, licensing and cost

- **BRREG Enhetsregisteret:** legal identity, legal form, address, industry, employee count, registered website and roles. The public dataset/API uses NLOD 2.0.
- **BRREG accounts API:** an earlier build mistakenly queried the PPE pre-production hostname, causing all 100 financial lookups to time out. BRREG's developer documentation distinguishes PPE from PRD and lists `https://data.brreg.no/regnskapsregisteret/regnskap` as production. The implementation now uses production. A fresh live probe in this run could not resolve either BRREG hostname (`getaddrinfo failed`), so the corrected endpoint has not yet been confirmed live. The API remains a preview with limited coverage and no service guarantee; errors are surfaced and values are never guessed. [BRREG environment documentation](https://github.com/brreg/regnskapsregister-api/blob/main/docs/for-devs.md) and [BRREG API catalog](https://github.com/brreg/openAPI/blob/master/specs/API-catalog.ttl).
- **Company-owned websites:** terms apply per site. The crawler checks `robots.txt`, rejects unsafe destinations and labels retrieval errors. It does not scrape LinkedIn, Meta, Indeed, Glassdoor or other restricted platforms.
- **Models/search:** none in the default run. The optional search helper requires `OPENAI_API_KEY`, has a small candidate budget and was tested only with mocked responses. It did not create published facts or incur charges.
- **Observed third-party API cost:** USD 0.00 in the final live run. This excludes local compute and network costs.

## 5. Final live 100-company test

The input is a reproducible reservoir sample of 100 unique organisation numbers selected with seed `20261017` while scanning all 411,160 rows of Builderr's public universe. The archived universe's SHA-256 is stored in `smoke_100_sample.json`. The result is a local smoke test, not an official Builderr evaluation or a hidden-label coverage benchmark.

The final command was:

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/smoke-100-final.jsonl --state outputs/smoke-100-final.sqlite3 --timeout 2 --workers 4
```

Measured results:

- 100 inputs, 100 outputs, 100 unique companies, preserved input order, and 100 verified legal identities.
- All 100 received seven terminal family statuses. No malformed states, missing evidence references or internal errors remained in the final result.
- Company details: available for 100; locations: 99 available and 1 not available; registered people/roles: available for 100.
- Website: 1 available, 92 not available, 4 ambiguous and 3 failed. Jobs: 1 available, 96 not available and 3 failed. Public activity: 97 not available and 3 failed.
- Financials: failed for all 100 because the documented accounts host timed out. There are no fabricated figures or zero-filled missing values.
- 313 outbound requests; 692,054 ms summed company processing time; 175,881 ms wall time with four workers; 103 recorded source errors; USD 0.00 paid API cost.
- 1,599 published facts across company details, locations, registered roles, website and jobs; every fact has an evidence ID, and every ID resolves to a source snapshot with URL, retrieval time and SHA-256.

The complete envelopes, run report and HTML review page are in `outputs/smoke-100-final.jsonl`, `outputs/smoke-100-final.jsonl.report.json` and `outputs/smoke-100-final.jsonl.html`.

## 6. Testing

The project suite now passes **24/24 tests** in 1.6 seconds. Tests cover valid and invalid organisation numbers, display normalization, input formats, exact-company finance matching, production-host selection, nested BRREG role names, website identity checks, private-host rejection, current-job evidence, snapshot idempotency, dashboard escaping, optional search-candidate rejection and every-input batch completeness. A live run also exercised the four-worker path. The corrected production endpoint could not be live-verified during this follow-up because DNS resolution for BRREG failed; see [FETCH_FAILURE_ANALYSIS.md](FETCH_FAILURE_ANALYSIS.md).

The official starter kit's deterministic refresh replay passed: 2 expected changes, 2 true positives, 0 false positives, 0 false negatives, complete evidence and an idempotent rerun. Its complete wider test suite could not run because `extruct` is not installed in this environment; that dependency is not required by this project.

## 7. Independent judge estimate

**Estimated score: 44/100; broad plausible range 35–55.** The estimate uses Builderr's published 50/30/12/8 weights, but hidden labels are unavailable, so recall points cannot be measured precisely. The estimated breakdown and basis are in [SIGNALPOST_JUDGE_REPORT.md](SIGNALPOST_JUDGE_REPORT.md). The point estimate is below the 65-point qualification threshold and is not an official score.

The main weakness is coverage. One website and one job are supported, no public activity was found, and the prior financial run failed against PPE. The safer identity and evidence gates protect precision but do not compensate for missing useful facts. Synthesis is concise but registry-heavy. The HTML browser and one-command runner make results reviewable, though there is no hosted product interface.

## 8. What remains before an entry can be called competitive

1. Re-run finance against the corrected production endpoint from a network that can resolve BRREG; verify one known filer and keep reporting period and currency on every value. If production is reachable but returns 404, distinguish true no-record cases from service failures before changing coverage status.
2. Add permitted company-owned news/activity and careers-page extraction with freshness dates, exact entity evidence and source citations. Add licensed search/news connectors only after verifying their access terms and cost.
3. Test source changes and retry behavior under the official resource budget, then run a larger local scale test against a clean clone. The 100-company duration alone is not a 1,000-company measurement.
4. Push the final exact code revision and record its commit hash. The GitHub write API returned HTTP 403 during this task, so the current public main branch is still the earlier baseline commit `b4767fc9ab3a4f0f3ebddf9f656456d9003c3fe1`. Add the owner's contact details to the manifest before entry; no personal email was assumed.
5. Submit only after the owner chooses to do so and Builderr provides an official run. No submission email or competition entry was sent during this work.

