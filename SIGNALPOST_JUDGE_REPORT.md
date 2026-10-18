# Signalpost judge report

**Prepared:** 18 October 2026
**Purpose:** independent local assessment of the prepared build; this is not Builderr's official score.

## Finding

The project is runnable and its 100-company smoke-test artifact is complete. It is not possible to state a truthful score out of 100 from this local run: Builderr uses a checked comparison collection unavailable to us, and the official evaluation is the only source of a qualifying score. The older 44/100 estimate in an earlier draft was not measured against Builderr's rubric and is withdrawn. No local numerical score should be presented as an official or predicted result.

## Measured smoke run

The run used the fixed 100-company sample selected from 411,160 public-universe rows (seed 20261017). It produced 100 result envelopes for 100 distinct inputs and verified all 100 legal identities. Runtime was 108,675 ms wall time with four workers and 322 outbound requests. It recorded four source errors. Paid API cost was USD 0.00 because the default path uses no paid provider.

| Data family | Available | Not available | Ambiguous | Failed |
|---|---:|---:|---:|---:|
| Company details | 100 | 0 | 0 | 0 |
| Registered location | 99 | 1 | 0 | 0 |
| People / registered roles | 100 | 0 | 0 | 0 |
| Financials | 99 | 0 | 0 | 1 |
| Website presence | 1 | 92 | 4 | 3 |
| Jobs | 1 | 96 | 0 | 3 |
| Public activity | 1 | 96 | 0 | 3 |

The single financial failure was a BRREG HTTP 500 response after a transient retry; no financial value was invented. Website, jobs, and activity failures reflect unavailable or blocked site retrievals. “Not available” means the configured permitted sources returned no usable fact; it does not prove the fact does not exist elsewhere. One activity profile included ten dated items from the company's registered website feed and passed the evidence-integrity checks. The optional NAV scan used four requests, was capped at three pages, found zero matching profiles, and reported truncation.

Machine-readable run details: `outputs/live-100-coverage-final.jsonl.report.json`. The profile JSONL contains one envelope per input. The HTML file is a convenience viewer, not the source of truth.

## Reliability and evidence controls

The agent matches on Norwegian organisation number using BRREG as the identity anchor. Published facts retain source URL, retrieval time, excerpts and a content-hashed source snapshot. A final gate checks that claim, evidence and snapshot agree on company number, source family and field; financial claims also have the requested entity checked. A fact that fails these checks is dropped. A missing, blocked or failed source remains explicitly unavailable, ambiguous, or failed instead of becoming a guessed value. Retries are bounded and counted.

The historical live-run revision passed 71 local tests. The current source tree passes 78 local tests after adding conservative website matching and optional Wikidata exact-ID discovery. These tests establish behavior on controlled cases; they do not establish live-world recall or coverage lift. The saved live 100-company run predates the optional Wikidata path.

## Competition readiness and limits

The current official detailed instructions ask for a 100-company smoke-test result and the reproducibility metadata in the manifest. They state that Builderr supplies companies for official runs and that precomputed profiles are not used in ranking. The same page also shows a shorter “1,000+ profiles” line, which conflicts with its detailed instructions. The bundle addresses the detailed 100-company smoke-test requirement and documents that ambiguity; ask Builderr to resolve it before interpreting the banner as a required static dataset.

The main performance weakness is coverage outside BRREG. Only one website was usable and only one jobs profile was found in this sample. The NAV test did not improve that result. Activity collection is limited to dated articles and feeds advertised by the BRREG-listed website. Restricted LinkedIn/Internshala scraping is intentionally not used. Precision, evidence integrity, and complete output shape are demonstrable locally; recall against Builderr's reference collection is not. The live run also demonstrates that an upstream API can fail for a specific company while the agent preserves a complete, honest profile.

The code is prepared but has not been sent to Builderr. Any score, qualification decision, and leaderboard position remain pending official evaluation.

Official challenge details: [Builderr Signalpost](https://builderr.ai/challenges/signalpost).

## Coverage diagnosis and current changes

The supplied 2025 universe contains 411,160 rows; 44,855 (10.91%) list a BRREG website. Among AS entities, the rate is 8.8%, and among industry-code-68 entities it is 4.9%. In the saved 100-company run, 92 profiles had no registered website, four were ambiguous, and three failed retrieval. The three fetched ambiguous pages did not contain the exact organisation number or distinctive legal name in their saved text. Therefore widening legal-name matching alone would have rescued none of these cases and cannot fix the dominant no-website gap.

Current code scans full page text for a distinctive normalized legal name (including a suffix-free variant) while preserving exact organisation-number matching and rejecting weak short-name mentions. It also offers `--wikidata-websites`: a website candidate is returned only from a Wikidata record whose Norwegian organisation-number property exactly matches the profile. The candidate remains untrusted until the site's existing safe-fetch, access-policy and page-identity checks pass. The connector has not been verified against live Wikidata because this environment could not resolve its endpoint. The measured live coverage figures above are unchanged; no uplift or score increase is claimed. The full current local suite passes 78 tests.
