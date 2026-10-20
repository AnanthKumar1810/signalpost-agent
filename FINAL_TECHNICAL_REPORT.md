# Signalpost engineering and qualification-readiness report

Prepared 20 October 2026. This report separates observed results from expectations. Builderr alone can assign the official score. Its [current challenge page](https://builderr.ai/challenges/signalpost) describes a 65/100 qualification threshold and a rubric of 50 recall, 30 precision/evidence, 12 synthesis, and 8 user experience. The local runs below are **not** Builderr evaluations.

## What was built

`signalpost_agent.py` accepts Norwegian nine-digit organisation numbers, resolves the exact legal entity from BRREG, and emits one JSONL profile per input. It collects registered identity, places, roles, annual accounts, registered or separately discovered company websites, first-party careers and dated activity. Every published fact carries retrieval time and evidence IDs pointing to a company-specific source snapshot. The final publication gate checks source family, organisation number, field and URL. Missing, blocked, ambiguous, or failed information stays explicitly marked; no financial values are guessed. A SQLite snapshot store records source changes across runs. The CLI writes a machine-readable report and a mobile-responsive, searchable HTML review view.

The recommended run needs Python 3.10+ and no third-party Python packages or paid API keys:

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites
```

`submission_1000.json` contains a deterministic 1,000-number sample drawn from Builderr's public eligible-company universe. The sample is a local stress test, not the hidden evaluation batch. The complete live result is `outputs/submission-1000-final.jsonl`; its report is the adjacent `.report.json` file.

## Key architecture decisions and measured effects

1. **BRREG number first.** Legal names and brand names collide. All claims remain anchored to a verified organisation number. BRREG registry, roles, and accounts are the high-yield baseline; the accounts preview can return HTTP 500 for an individual company. A retry counts as an additional request, and a persistent failure remains `failed` without a fabricated figure.
2. **Website candidates are leads.** BRREG's website field, exact-number Wikidata lookup, and optional search can propose URLs. HTTPS, public-address and robots checks precede fetching. The fetched page needs the exact organisation number or distinctive full legal name (including a conservative suffix-free form). Search titles/snippets never become facts. A registered legacy domain may redirect to a different public HTTPS host, but the final page is rechecked against the legal entity. Untrusted discovered sites cannot cross-host redirect. This change recovered Arena Trondheim in the 10-company diagnostic.
3. **Bounded external collection.** Same-site jobs/activity pages and advertised RSS/Atom feeds are checked only after website verification; linked public ATS boards may be queried. NAV's feed scans a bounded 180-day change window, at most 25 pages and 100 details by default. Its 100-company run reported truncation, so zero matches is not proof of zero jobs. Wikidata returned no exact-number website candidates in the 100- or 1,000-company runs.
4. **No unsupported hiring assertion.** A dated, unexpired first-party vacancy may be called active. An undated advertisement has explicitly unconfirmed freshness. A generic “Jobs” or “Ledige stillinger” landing page is labeled `careers_page_url`, not a vacancy. This correction landed after the 1,000-company process had loaded its code; the final 100-company rerun tests it.
5. **TinyFish stays optional.** Ten direct searches produced mostly directories/aggregators. Initial integration mistakenly accepted five such pages as company sites. The domain and directory gates were tightened, and an isolated rerun found zero additional verified company-owned sites. A later 100-company integration attempt ran concurrently with a 1,000-company load and suffered DNS failures; it is not a fair recall comparison. Builderr cannot rely on this participant's private TinyFish key, so TinyFish is absent from the evaluator command. No secret is stored in the repository.
6. **Synthesis remains evidence-bound.** The default summary uses verified facts and names unknown areas. An optional API-key-gated LLM summary is fixture-tested but not live-tested without a model key. It is disabled in every benchmark below. The LLM cannot supply missing evidence.
7. **Transport resilience.** Concurrent Windows DNS failures destroyed the first 1,000-company attempt. The final run caches only pre-validated public addresses for fixed provider hosts for five minutes. Arbitrary company website hosts remain subject to a fresh safe-resolution and pinned connection. This recovered a 130-company failed slice in 124 seconds with 130/130 verified identities; the subsequent fresh 1,000-company run completed 1,000/1,000.

`REFERENCE_EVALUATION.md` records the separate, bounded checks of Fire Enrich, Company Research Agent, Dataforge, python-brreg, and Crawl4AI. Their useful ideas informed source separation, evidence curation, checkpoints, and bounded extraction; integrating five complete frameworks would add setup, network, and model dependencies without demonstrated recall gain. Full credentialed graphs were not benchmarked.

## Controlled live comparisons

Both A and B use the same checked-in `smoke_100.json`. A isolates official data; B adds deterministic website, NAV, and Wikidata checks. Their outputs and `.report.json` files are retained in `outputs/`.

| Metric | A: official only | B: deterministic web + NAV + Wikidata |
|---|---:|---:|
| Complete profiles / verified identities | 100 / 100 | 100 / 100 |
| Outbound requests | 301 | 350 |
| Wall time | 48.156 s | 143.560 s |
| Published facts | 2,907 | 2,928 |
| Financial families available | 99 | 99 |
| Verified website families | 0 | 2 |
| Jobs families available | 0 | 2, both generic undated careers pages in this pre-fix run |
| Public-activity families available | 0 | 1 |
| Structural lineage violations | 0 | 0 |

Commands:

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/benchmark-100-a-official.jsonl --state outputs/benchmark-100-a-official.sqlite3 --timeout 10 --workers 8 --no-website
python signalpost_agent.py --input smoke_100.json --output outputs/benchmark-100-b-deterministic.jsonl --state outputs/benchmark-100-b-deterministic.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites
python audit_profiles.py outputs/benchmark-100-b-deterministic.jsonl
```

B adds 21 facts at the cost of 49 more requests and roughly 95 seconds in this sample. It found two verified sites and one dated activity feed. The two job pages have generic titles and must not be described as confirmed openings; that motivated the classification fix. NAV made 28 requests, found two potential employer candidates, matched zero profiles, and reported truncation. Wikidata made two queries covering 92 site-less companies and found zero candidates. One BRREG accounts lookup repeatedly returned HTTP 500; it remains failed. There were two website fetch failures and four ambiguous website leads.

After the generic-title fix, `python signalpost_agent.py --input smoke_100.json --output outputs/final-100-smoke.jsonl --state outputs/final-100-smoke.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites` returned 100 verified identities, 2,928 facts, 350 requests, and 139.056 seconds wall time. Both generic job pages are now recorded as `careers_page_url`, and the independent audit found zero lineage violations. This is the current-code smoke artifact.

TinyFish C, run with the same 100 inputs, returned 100 verified identities but took 505.640 seconds and 484 requests. Its search stage made 92 requests, returned 39 candidate URLs, and verified **zero** new sites. Many searches and NAV DNS lookups failed during a simultaneous load test, so C is an environmental stress observation, not a controlled effectiveness estimate. The separate 10-company diagnostic and candidate-rejection log are in `outputs/tinyfish-10-company-test/REPORT.md`.

## Fresh 1,000-company scale run

The run used one CLI invocation with eight workers and the same no-key feature set:

```powershell
python signalpost_agent.py --input submission_1000.json --output outputs/submission-1000-final.jsonl --state outputs/submission-1000-final.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites
python audit_profiles.py outputs/submission-1000-final.jsonl
```

The completed report records **1,000/1,000 terminal profiles, 1,000 verified identities, 3,313 outbound requests, 662.498 seconds wall time, and 29,719 published facts**. The independent structural audit found zero missing or cross-company evidence links. Its categories were: company details 1,000 available; locations 998; people 999; finances 993; verified website presence 38; jobs 6 (pre-classification-fix run, so these are not automatically active vacancies); public activity 12. Website outcomes also included 894 not available, 41 ambiguous, 24 failed, and 3 blocked. The run reported 34 source/processing errors; none erased a verified identity. NAV made 28 requests and matched zero profiles; Wikidata made 18 queries for 894 site-less numbers and found zero candidates. No paid provider was called. The output is approximately 11 MB.

This proves that one run can produce 1,000 profiles within about 11 minutes on this machine. It does **not** show that 1,000 companies each have all desired categories, nor that a future evaluator network has the same speed or access. The earlier concurrent-load attempt failed many DNS lookups; that failure and the final successful rerun are both disclosed above.

## Tests, failures, and residual risk

The local standard-library test suite exercises entity matching, same-company evidence, HTTP errors, robots behavior, redirect safety, first-party jobs and feeds, ATS links, discovery filtering, and summary safeguards. `python -m unittest discover -s . -p 'test_*.py' -q` passed 106 tests on the final source. `audit_profiles.py` checks output lineage independently of the publication gate; it cannot prove every source statement is true. The frozen dashboard is a review sample, not a live hosted product until GitHub Pages is enabled.

The most serious qualification risk is **recall**. Only 38 of the 1,000 random eligible companies yielded a verified website, six yielded some jobs-family information, and twelve yielded public activity; the 100-company B sample yielded two, two generic careers pages, and one respectively. These outcomes arise partly because many entities have no BRREG website, discovery sources return directories rather than company-owned pages, and the pipeline rightly rejects unverified identity. Broadening the identity gate or treating search snippets as evidence would risk a disqualifying wrong-company claim. More permitted source coverage with independent company-number verification is needed for a credible recall improvement. No local number out of 100, including 65, can be honestly assigned without Builderr's checked comparison and UX review.

No competition submission email was sent. The participant must review the current official deadline and submission instructions before sending anything.
