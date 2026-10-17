# Signalpost agent: implementation and verification report

Prepared 17 October 2026. This report distinguishes fixture results from live service results. It does not claim an official Builderr score or qualification.

## Current status

The repository is now a more complete, reproducible baseline. It validates Norwegian organisation numbers, anchors identity in BRREG, collects roles and available registry facts, retrieves public annual-account key figures, conservatively checks a BRREG-declared company website, attaches dated source evidence, keeps content-addressed source history, produces deterministic summaries, and returns one terminal envelope for every company input. All seven claim families get exactly one terminal status per result.

It is not possible to certify full submission readiness in this environment. The official evaluation contract is linked from Builderr’s page as Markdown, but the browser fetch and shell network are blocked for that file. The live BRREG and Builderr hosts also fail DNS resolution here. Thus the real 100-company smoke run, clean-network dependency check, and evaluator-schema validation remain outstanding. The output contract below follows the visible official rules and needs a final field-by-field check against Builderr’s downloadable contract.

## Competition requirements checked

Builderr’s current page says the agent must accept the company batch supplied at run time, return one complete result per company, keep claims tied to the right entity with sources and dates, preserve evidence across idempotent refreshes, and finish within the evaluator’s time and resource limits. Submission calls for a 100-company smoke-test report, repository link, exact commit, one run command, model/API/licence details, expected run cost, and contact details. Qualification requires at least 65/100 on an official run. The current score weights are 50 recall and coverage, 30 precision and evidence, 12 synthesis, and 8 UX. These facts are from the [current Signalpost challenge page](https://builderr.ai/challenges/signalpost), read 17 October 2026.

The published technical rules require exactly one terminal envelope per input and distinct `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, and `failed` statuses. The implementation uses all six status values and applies one status to each of its seven families. Its envelope includes the input organisation number, identity anchor, claims and facts, evidence, source snapshots, errors, changes, a deterministic summary, run metadata, and request/cost metadata. The official downloadable contract could not be inspected, so compatibility with any additional required field names has not been verified.

## Implementation choices

The system uses only Python’s standard library. There are no dependency pins to drift and no model or paid search credentials. The primary identity source is the official BRREG entity endpoint; public roles come from its roles endpoint. Financial figures use the separate BRREG public key-figures API, which can return the latest approved accounts for entities that follow ordinary accounting presentation; its upstream specification warns that this service is a preview without service guarantees and coverage excludes some industries and consolidated figures. Values are published only when the returned statement’s organisation number matches the requested number. Facts include the retrieval date and reporting period when available. [BRREG dataset licence](https://www.brreg.no/en/use-of-data-from-the-bronnoysund-register-centre/datasets-and-api/) · [BRREG key-figures API specification](https://raw.githubusercontent.com/brreg/regnskapsregister-api/main/src/main/resources/specification/regnskapsregister.json)

Company-site requests are limited to the exact HTTPS host listed by BRREG. The fetch checks `robots.txt`, resolves public IP addresses, rejects unsafe redirects, bounds response size, and requires the legal name or organisation number on the page before publishing descriptive facts. A careers link is not counted as an active job. The system does not yet discover or verify active vacancies, parse annual-account PDFs, or collect dated company activity. It marks these areas unavailable rather than guessing. Public website terms apply. No social platform is scraped.

The deterministic summary uses only evidence-backed registry fields and identifies unknown claim families. Hash-addressed snapshots and a current-source pointer prevent duplicate snapshots and false changes on identical refreshes; changes retain the earlier hash and identify changed fields. Every input receives a complete envelope when a per-company exception occurs.

## Reference project decisions

This remains an architecture audit and adapted-component test. The five upstream projects were not cloned or executed: service credentials, external network and/or additional frameworks were not available. No upstream test result is claimed.

| Reference | Relevant design | Decision and evidence here |
|---|---|---|
| [Fire Enrich](https://github.com/firecrawl/fire-enrich) | Specialized company research modules using Firecrawl and an LLM, with source attribution. | Adopted typed fact groups and evidence references. Excluded email/domain-first identity and per-company paid multi-agent discovery because legal-entity proof and reproducible cost matter more. No upstream run; no provider keys. |
| [Company Research Agent](https://github.com/guy-hartstein/company-research-agent) | Analyzer → collector → curator → briefing → editor graph with Tavily and a selectable model. | Adapted separation into identity → register facts/roles → financials → optional site → summary. Excluded full graph orchestration to keep the batch deterministic and dependency-light. No upstream run; no provider keys. |
| [FORGE / Dataforge](https://github.com/Nuclear-Marmalade/dataforge) | Deterministic collection, optional local AI, checkpoints, database persistence, and recovery. | Adapted immutable snapshots, per-company error containment, and explicit refresh changes. Replaced fill-empty-only updates with content versions. No upstream run; its database and setup were outside this baseline. |
| [python-brreg](https://github.com/crdbrd/python-brreg) | Python wrapper for entity and role lookup by Norwegian organisation number. | Implemented a small standard-library adapter against official BRREG APIs so the legal key stays central and no additional package is required. No upstream installation or live API run; DNS blocked. |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | Async/browser crawling with deterministic extractors and optional typed LLM output. | Adapted bounded static-page parsing and deterministic extraction. Excluded browser setup and deep crawling until an evaluation shows this lane increases supported recall enough to justify its runtime and failure surface. No upstream run; browser install/network unavailable. |

The combined lesson is that the projects serve separate layers. A minimal version of each useful idea makes the system simpler and more auditable than bringing five overlapping runtimes into one batch process. The current design prioritizes legal identity and evidence accuracy. That choice limits recall until permitted evidence lanes for jobs and public activity are added and measured.

## Test commands and measured results

Run the local suite with:

```text
python -m unittest -v
```

Result on Python 3.13 from a clean-copy checkout: **10 tests passed in 1.789 seconds**. The tests cover organisation-number rejection before network access, registry field/evidence mapping, financial value extraction, rejection of a financial record for another entity, exact website identity gating, private-address rejection, snapshot idempotency and change detection, safe dashboard generation, and a 100-company fixture batch returning one complete result per input.

The 100-company test is fully mocked. It measures batch envelope behavior, not live recall, source uptime, or live-company accuracy. Its organization numbers are generated valid-format fixtures, not claimed to be 100 real eligible companies.

The live command used on the five-company sample was:

```text
python signalpost_agent.py --input smoke.json --output outputs/clean-check.jsonl --state outputs/clean-check.sqlite3 --timeout 1
```

Measured result from a clean-copy checkout: 5 inputs, 5 terminal outputs, 5 failed identity lookups, 5 recorded requests, 5 errors, 206 ms aggregate request time, and $0.00 paid API cost. The errors were caused by unavailable DNS resolution for BRREG in this execution environment. This confirms complete failure reporting only; it does not verify any live fact or satisfy the required live 100-company smoke report.

The reproducible command for a supplied batch is:

```text
python signalpost_agent.py --input <builderr-batch.jsonl> --output outputs/run.jsonl --state outputs/state.sqlite3 --timeout 3
```

The output path creates a JSONL file, machine-readable run report, and local HTML viewer. The implementation has no paid third-party API calls, so expected API cost is **$0 per run**. Compute and wall-clock time are environment-dependent; live 100-company runtime is unknown. Set `SIGNALPOST_CONTACT` to a valid contact before running or submitting.

## Remaining submission blockers

1. Run the exact command on Builderr’s real 100-company smoke batch with network access. Confirm 100 complete outputs, runtime, errors, API requests, and zero silent drops.
2. Compare this envelope against the downloadable evaluation contract and adjust any required keys or input format.
3. Test the pinned commit from a clean clone with the real supplied input and evaluator time budget.
4. Add the submitter’s contact details. The GitHub repository is private, so provide evaluator access or make it public before sending the entry.
5. Add permitted, measured evidence lanes for financial statement history, verified active jobs, and dated company activity if they improve recall without material wrong-company matches.

The GitHub repository created earlier remains at its previous commit (`fe9b86efd2a8f1bc7b37597fbfa08b4fba694f35`). The connected GitHub integration returned 404 for this private repository on read and write requests, so the verified revision in this package was not pushed. The repository must remain private unless its owner chooses otherwise; no visibility change was made.

Do not claim a 65/100 score until Builderr reports it from an official run.
