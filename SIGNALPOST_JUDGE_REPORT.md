# Signalpost judge report

**Prepared:** 19 October 2026
**Purpose:** independent local assessment of the prepared build; this is not Builderr's official score.

## Finding

The project is runnable and its 100-company smoke-test artifact is complete. A truthful Builderr score out of 100 cannot be calculated locally: Builderr compares against its checked findings, which are unavailable, and says the local smoke test is not the competition score. The older 44/100 estimate in an earlier draft was not measured against Builderr's rubric and is withdrawn. No numeric official or predicted score is claimed here.

The current official rubric is 50 recall, 30 precision/evidence, 12 synthesis, and 8 UX, with qualification at 65/100 on an official run. Builderr's current instructions require the reusable agent and a 100-company smoke report; the 1,000+ profiles banner refers to testing at scale, while the detailed submission section says precomputed profiles are not needed for ranking. The current page states the evaluated batch is 300 companies and its public table is based on validated evidence from that run. Those public leaderboard values cannot be mapped onto this project's code without running Builderr's harness.

## Measured smoke run

The original baseline run used the fixed 100-company sample selected from 411,160 public-universe rows (seed 20261017). It produced 100 result envelopes for 100 distinct inputs and verified all 100 legal identities. Runtime was 108,675 ms wall time with four workers and 322 outbound requests. It recorded four source errors. Paid API cost was USD 0.00 because the default path uses no paid provider. The most recent run with NAV, Wikidata discovery, public ATS adapters and current profile code is recorded separately below; it must not be confused with this baseline.

| Data family | Available | Not available | Ambiguous | Failed |
|---|---:|---:|---:|---:|
| Company details | 100 | 0 | 0 | 0 |
| Registered location | 99 | 1 | 0 | 0 |
| People / registered roles | 100 | 0 | 0 | 0 |
| Financials | 99 | 0 | 0 | 1 |
| Website presence | 1 | 92 | 4 | 3 |
| Jobs | 1 | 96 | 0 | 3 |
| Public activity | 1 | 96 | 0 | 3 |

The single financial failure was a BRREG HTTP 500 response after a transient retry; no financial value was invented. Website, jobs, and activity failures reflect unavailable or blocked site retrievals. “Not available” means the configured permitted sources returned no usable fact; it does not prove the fact does not exist elsewhere. One activity profile included ten dated items from the company's registered website feed and passed the evidence-integrity checks. The historical NAV scan used four requests, was capped at three pages, found zero matching profiles, and reported truncation.

Machine-readable baseline details: `outputs/live-100-coverage-final.jsonl.report.json`. The profile JSONL contains one envelope per input. The HTML file is a convenience viewer, not the source of truth.

### Fresh combined benchmark (19 October)

The current code was run with `--nav-jobs --wikidata-websites` on the same 100-company input. It returned 100/100 complete envelopes and verified all 100 identities in 177,328 ms using four workers. The run report counted 348 outbound requests, including 28 NAV requests and 2 Wikidata queries, and four source errors. Measured third-party paid API cost was USD 0.00. One finance lookup still returned HTTP 500. Website presence remained 1 available, 92 not available, 4 ambiguous and 3 failed; jobs and public activity each remained 1 available, 96 not available and 3 failed. NAV saw two employer candidates, matched zero companies and reported truncation at 25 pages. Wikidata queried 92 profiles without a BRREG website and returned zero exact-ID candidates. The ATS adapters were exercised by fixtures but found no new live board-linked job in this batch. Compared with baseline, the new sources caused **no measured external-coverage increase**.

The combined profile file has 2,923 facts. A local structural check found zero facts without evidence IDs, zero IDs without a matching source snapshot, zero source snapshots belonging to a different company, and zero `evidence_integrity` errors. This checks lineage structure, not the truth or completeness of each fact. Only 1 of 100 profiles had information in all five grouped information areas. Reproduction: `python signalpost_agent.py --input smoke_100.json --output outputs/live-100-combined-final.jsonl --state outputs/live-100-combined-final.sqlite3 --timeout 8 --workers 4 --nav-jobs --wikidata-websites`.

## Reliability and evidence controls

The agent matches on Norwegian organisation number using BRREG as the identity anchor. Published facts retain source URL, retrieval time, excerpts and a content-hashed source snapshot. A final gate checks that claim, evidence and snapshot agree on company number, source family and field; financial claims also have the requested entity checked. A fact that fails these checks is dropped. A missing, blocked or failed source remains explicitly unavailable, ambiguous, or failed instead of becoming a guessed value. Retries are bounded and counted.

The historical live-run revision passed 71 local tests. The current source tree passes 83 local tests after adding conservative website matching, optional Wikidata exact-ID discovery, linked public job-board support, and expanded profile-browser behavior. These tests establish behavior on controlled cases; the three live comparisons above show no recall lift in this sample.

## Competition readiness and limits

The current official detailed instructions ask for a 100-company smoke-test result and reproducibility metadata in the manifest. Builderr supplies companies for official runs and precomputed profiles are not used in ranking. The public page also advertises a 1,000+ profile scale option; that is a local testing scale, not a static dataset submission requirement.

The main measured weakness is coverage outside BRREG. Only one website was usable and only one jobs profile was found in this sample. The current code expands NAV to a bounded 180-day active-posting window and supports public Greenhouse, Lever, and Ashby boards only when a verified company website links to them; a fresh 100-company run did not improve coverage. Activity collection still depends on dated pages or feeds from a verified company site. Restricted LinkedIn/Internshala scraping is intentionally not used. The live run demonstrates that an upstream API can fail for a specific company while the agent preserves a complete, honest profile.

The code is prepared but has not been sent to Builderr. Any score, qualification decision, and leaderboard position remain pending official evaluation.

Official challenge details: [Builderr Signalpost](https://builderr.ai/challenges/signalpost).

## Coverage diagnosis and current changes

The supplied 2025 universe contains 411,160 rows; 44,855 (10.91%) list a BRREG website. Among AS entities, the rate is 8.8%, and among industry-code-68 entities it is 4.9%. In the saved 100-company run, 92 profiles had no registered website, four were ambiguous, and three failed retrieval. The three fetched ambiguous pages did not contain the exact organisation number or distinctive legal name in their saved text. Therefore widening legal-name matching alone would have rescued none of these cases and cannot fix the dominant no-website gap.

Current code scans full page text for a distinctive normalized legal name (including a suffix-free variant) while preserving exact organisation-number matching and rejecting weak short-name mentions. In the saved sample, the four ambiguous sites did not contain the exact organisation number or distinctive legal name, so widening the check alone did not address that measured gap. The `--wikidata-websites` option discovers website candidates from exact Norwegian organisation-number matches; each candidate remains untrusted until safe-fetch, access-policy and page-identity checks pass. Wikidata is now live-verified on the 100-company sample and returned no candidates. Supported ATS boards explicitly linked from verified company pages and the wider bounded NAV window also returned no additional jobs in this batch. Current coverage figures therefore remain unchanged. The full current local suite passes 83 tests.

## Current score status

| Builderr category | What this project can demonstrate locally | Why the score is unavailable |
|---|---|---|
| Recall and coverage (50) | Historical 100-company family counts are recorded; new ATS/NAV logic has adversarial unit coverage. | The evaluator's checked facts and unseen 300-company test batch are not available; the fresh source changes have not been live-benchmarked. |
| Precision and evidence (30) | 2,909 historical facts passed the structural evidence-link audit; cross-company/source/field mismatch tests fail closed. | Structural linkage is not a labeled truth/precision audit of each claim against Builderr's judged facts. |
| Synthesis (12) | Current deterministic summary includes sourced identity/activity/finance/leadership when present and identifies unknown families. | No Builderr synthesis grader or calibrated human-scoring run has been applied. |
| UX (8) | Generated HTML includes search, category-gap/error filters, coverage sorting, compare up to three companies, facts, evidence links and retrieval dates; responsive layout is implemented. | No hosted product or recorded desktop/mobile usability evaluation has been run. |

Thus the correct score today is **not measured**, not zero and not an estimated number. The historical sample is a functional smoke test. A local claim that it “scores 20/50 recall” or “qualifies” would be unsupported.
