# Signalpost engineering report

Prepared 17 October 2026. This report describes the implementation and tests completed in the current workspace. It distinguishes measured fixture behavior from live competition performance.

## Result and current competition contract

The current official Builderr page says the challenge closes on 21 October, with a 100-company smoke-test report, one reproducible command, repository and exact commit, and disclosure of models, APIs, licences and expected cost. Builderr supplies the scored companies and does not use precomputed profiles for ranking. Current scoring is 50 recall and coverage, 30 precision and evidence, 12 synthesis and 8 UX; qualification requires 65/100 on an official run. Every supplied input must yield one terminal result, missing evidence must not be guessed, and refreshes must avoid duplicate or false changes. The current page allows up to four revised commit hashes before 18 October. These current rules differ from the older Unstop screenshot in the referenced chat, so this implementation follows Builderr's current challenge page and its instruction that the challenge page controls. [Current Signalpost rules](https://www.builderr.ai/challenges/signalpost) · [Builderr shared rules](https://www.builderr.ai/guidelines)

I built a small reproducible baseline, not a qualified competition entry. I did not send a submission or email.

## Implemented system

The implementation is in `signalpost_agent.py`. It takes a JSON array, JSONL, or text file of Norwegian organisation numbers; validates the Norwegian mod-11 check digit; anchors each result in the official BRREG entity endpoint; obtains the public roles endpoint; maps register fields to evidence-backed claim families; and, when BRREG lists a website, checks `robots.txt` and makes a bounded HTTPS request to that same host. Website facts are published only if the page title or H1 identifies the legal name exactly, or the organisation number appears in the page. It rejects private IP destinations and cross-host redirects.

The output is one JSONL envelope for each input, including explicit `failed`, `blocked`, `ambiguous`, `not_available`, and `not_applicable` states. Each fact references a source snapshot with URL, retrieval time and SHA-256. SQLite stores immutable content snapshots plus a current pointer, so a repeated identical source has no false change while a changed source receives a change record. The runner also writes a machine-readable run report and a responsive local profile browser. No LLM is called; model/API cost is therefore $0 for this implementation. Network and compute costs are not estimated.

The BRREG endpoints used are the official `/api/enheter/{orgnr}` and `/api/enheter/{orgnr}/roller` open-data services. BRREG documents the open-data licence as NLOD 2.0. [BRREG API documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html)

## Reference-by-reference evaluation

I inspected each project's published repository architecture, then tested the relevant pattern in isolation in this implementation. I did not clone or execute any of the five upstream projects: their published setups require combinations of external API credentials, full framework dependencies, a database, browser installation, or network access. DNS to BRREG is blocked from this execution environment. This is an honest architecture audit and adapted-component test, not a claim that five upstream apps were installed and benchmarked.

| Reference | How it works and useful ideas | Test and decision for Signalpost |
|---|---|---|
| [Fire Enrich](https://github.com/firecrawl/fire-enrich) | Starts with a work email and extracts its domain, then runs sequential discovery, company profile, funding, technical and custom-field agents. Searches within each stage can run concurrently; Firecrawl collects page content and OpenAI/GPT-4o extracts and synthesizes cited fields. Its README requires both Firecrawl and OpenAI keys. | **Not run upstream:** its required service keys were unavailable. **Do not port its identity strategy:** a domain derived from an email cannot prove which Norwegian legal entity owns a fact. Adopted the idea of typed field groups and source-linked facts. Rejected the multi-agent search loop as a default for every company because it adds latency, cost and entity ambiguity. |
| [Company Research Agent](https://github.com/guy-hartstein/company-research-agent) | A FastAPI endpoint runs a LangGraph research path: analyzers → collector → curator → briefing → editor. Tavily finds and ranks sources; an OpenRouter or direct OpenAI/Gemini model generates queries and synthesizes the report. React tracks job progress and exports a PDF. Tavily and a model key are required for a real run. | **Not run upstream:** no Tavily/model credentials, and no reachable network. Adapted the staged separation as a small deterministic sequence: identity → register facts/roles → optional company page → evidence-backed claims. Rejected the full graph for a per-company batch path where the official run has a fixed time budget. |
| [FORGE / Dataforge](https://github.com/Nuclear-Marmalade/dataforge) | Two parallel tracks enrich shared business records: deterministic website scraping and optional local Gemma via Ollama. It targets broad lead enrichment, includes PostgreSQL adapters, and documents checkpointing, watchdog recovery and `COALESCE` writes that only fill empty values. The repository is MIT licensed. | **Not run upstream:** its normal setup expects a PostgreSQL database and its data model targets broad sales leads, not Norwegian legal entities. Adapted bounded deterministic extraction, per-record failure containment, checkpointed content-addressed snapshots, and refresh comparison. Replaced fill-empty-only logic with versioned evidence and change detection, since a changed company fact must not be silently frozen. |
| [python-brreg](https://github.com/crdbrd/python-brreg) | A Python client around BRREG's Enhetsregisteret open API. Its published feature list includes entity search, lookup by organisation number, and roles by organisation number. It provides a clean typed client boundary but intentionally implements only parts of BRREG's wider API. | **Not run upstream:** its live HTTP calls could not be reached from this environment; package installation was not needed for the baseline. The API itself was fixture-tested through a direct standard-library adapter. This became the identity and legal-facts foundation because it matches Signalpost's exact Norwegian identifier. |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | An asynchronous crawler and extraction framework with browser-backed fetching, clean Markdown, CSS/XPath/regex structured extraction, optional typed LLM extraction, deep crawl strategies, browser setup and caching. The self-hosted version is open source; hosted search/scrape services have separate costs and limits. | **Not run upstream:** browser installation and external access are unavailable here. Adapted its most relevant order of operations—deterministic parsing before AI—as a small static HTML parser. A browser crawler is not needed for a first pass over a single registry-declared site. Consider it later only if measured coverage shows that static pages miss relevant evidence and the time budget permits it. |

The cross-project lesson is that these tools solve different layers. BRREG resolves legal identity; Fire Enrich and Company Research Agent orchestrate discovery and synthesis; FORGE contributes durable batch operations; Crawl4AI extracts pages. Putting all five frameworks into one stack would duplicate crawling and orchestration while multiplying dependencies and failure modes. The selected system therefore uses the registry as a hard identity anchor, deterministic evidence extraction, bounded website checks and durable snapshots. A targeted LLM could later summarize verified evidence, but it must never choose the entity or invent unsupported facts.

## Controlled tests and measured results

Tests were run on Python 3.13. The exact unit-test command was:

```text
python -m unittest -v
```

Result: **7 tests passed in 0.102 seconds**. The tests cover invalid organisation-number rejection without a request; register response mapping; fact-to-source references; website-vs-registry comparison; rejection of mismatched website identity; private IP rejection; and snapshot idempotency/change detection.

| Variant | Fixture result | Conclusion |
|---|---|---|
| A — BRREG only | 5 facts from a mocked official register response. Identity was verified; each fact resolved to a source snapshot. | Accurate baseline identity and legal-register facts; external company coverage remains limited. |
| B — BRREG plus deterministic website | **8 facts** on the same mock company: 5 register facts plus page title, page description and a careers-page link. A deliberately mismatched site produced `ambiguous` and published no website claims. | The crawl added 3 evidence-backed facts without publishing a wrong-entity site in the fixture. This is a unit-fixture result, not measured live coverage. A careers link is not treated as a live vacancy. |
| C — targeted LLM | Not implemented or run. | No test showed an LLM would improve recall enough to justify its cost. It is deferred until deterministic evidence is available and a measured extraction gap is known. |

The requested five-company operational command was:

```text
python signalpost_agent.py --input examples/smoke.json --output outputs/smoke.jsonl --state outputs/smoke-state.sqlite3 --timeout 1
```

It produced **5 output envelopes for 5 unique inputs**, 5 explicit errors, 5 `failed` identity states and 5 BRREG requests, with 113 ms aggregate recorded request runtime. The live API call failed with `getaddrinfo failed` because this execution environment cannot resolve external hosts. Thus the run verifies complete output and graceful failure behavior only; it does **not** verify live company facts, source recall, runtime at scale, or the required 100-company smoke test. Its generated output/report/browser files are retained in `outputs/`.

## Changes made and why

1. **Legal identity first.** Added a mod-11 organisation number check and made the BRREG entity response the identity anchor. This directly prevents name-only matching from assigning parent, brand or similarly named company facts to an entity.
2. **Evidence travels with facts.** Added source IDs, retrieval timestamps, report-period slots, snapshot URLs and hashes. This makes claims auditable and supports source freshness checks.
3. **Conservative website matching.** The only candidate website comes from BRREG. The fetch checks robots policy, requires HTTPS/public IPs, limits response bytes, blocks cross-host redirects and publishes facts only on exact legal name in title/H1 or organisation number. A candidate link alone cannot establish the entity.
4. **Refresh without silent overwrite.** Added hash-addressed history and a current-source index. Same content remains idempotent; changed content identifies changed top-level fields and keeps prior evidence.
5. **Batch operations and readable output.** Added one terminal envelope for every input, per-company errors, runtime/request/cost accounting, a JSON run report and a responsive evidence browser.
6. **No model during baseline.** The implementation makes no API/model calls, so it has no paid API cost and no model hallucination path. This reduces synthesis points potential, but is preferable until evidence-based evaluation demonstrates a safe benefit.

## Remaining work before submission

This is not ready to submit as-is. The live 100-company smoke test is outstanding; the sandbox network restriction prevented it. The official downloadable starter/evaluation contract contains the machine-readable envelope schema, but the browser search tool did not retrieve the linked Markdown/archive, so the custom envelope must be checked against that exact schema before entry. The official schema may require field names or shapes that differ from these envelopes.

The current agent is intentionally sparse: it does not parse annual-account PDFs into revenue/profit figures; verify active job advertisements; extract dated company activity; or synthesize an evidence-grounded company explanation. Those fields are `not_available` rather than guessed. A static local profile browser is generated, but no visual browser test was possible. The fetch path is sequential; scale timing against Builderr's actual time/resource budget is unknown. Finally, per-company website terms and the official permitted-source list must be checked before treating every company site as an allowed source.

The next practical sequence is: verify this output against Builderr's downloadable contract; run the 100-company smoke batch with network access; record time, requests, errors and source coverage; add official annual-account extraction and one permitted external evidence lane only when the measured added recall justifies its cost; then send a revised exact commit before the 18 October revision cutoff. Do not claim a 65/100 score until Builderr reports one from an official run.
