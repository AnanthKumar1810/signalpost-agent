# Signalpost project completion report

Prepared 17 October 2026 (Asia/Kolkata)

Repository: https://github.com/AnanthKumar1810/signalpost-agent  
Verified implementation commit: dcf649deabd41d5f8e0f56d5304e9a2fbbdcdf06  
Status: implementation completed, pushed, and verified in GitHub. Competition performance and qualification remain unverified.

## What is complete

The repository contains a reproducible Python baseline for researching Norwegian companies from their organisation numbers. It anchors identity in Norway's official Brønnøysund Register (BRREG), collects registry facts and roles, retrieves BRREG's public annual-account key figures where available, and optionally checks one company website only if BRREG lists it. It emits structured, evidence-linked results, captures source dates and reporting periods where available, stores source snapshots in SQLite, detects changes across runs, and generates a deterministic summary and local HTML viewer.

The project files were committed to the repository's main branch. The repo root now contains .gitignore, ENGINEERING_REPORT.md, README.md, signalpost_agent.py, smoke.json and test_signalpost_agent.py. The repository itself and its history were retained; the project files replaced the prior working tree contents. The current repository is public.

This completes the implementation baseline. It does not yet establish that Builderr's evaluator accepts the output, that live results meet the official score threshold, or that the project qualifies. The exact evaluator contract and required live benchmark still need to be run.

## How the architecture was selected

The original request was to study and test five related projects separately, learn from them, integrate their useful techniques, and report what was learned. We reviewed Fire Enrich, Company Research Agent, FORGE/Dataforge, python-brreg and Crawl4AI as architecture references. We did not clone or execute those five upstream systems; dependencies, service credentials and live network access were not available. No third-party result is claimed as a test of this project.

| Reference | Useful idea | Decision |
|---|---|---|
| Fire Enrich | Specialized fact groups, source attribution, crawling plus extraction | Adapt typed claim families and evidence references. Exclude email/domain-first identity and paid multi-agent research for every company. |
| Company Research Agent | Separate analysis, collection, curation and briefing steps | Use a smaller deterministic pipeline: identify, collect registry/financial/site evidence, curate, summarize. Exclude a full LangGraph/Tavily workflow per company to control cost and latency. |
| FORGE / Dataforge | Deterministic extraction, checkpoints, recovery, persistence | Adapt per-company error containment and versioned source snapshots. Prefer explicit content-change detection over fill-empty-only updates. |
| python-brreg | Norwegian company and role lookup by organisation number | Keep BRREG and the legal number as the identity anchor, but use a small standard-library client instead of adding a wrapper dependency. |
| Crawl4AI | Structured crawl and extraction | Use one bounded static page and deterministic parsing. Exclude browser installation and deep crawling until measured recall justifies their extra cost and failure surface. |

The combined design keeps the best relevant ideas while using one standard-library runtime. The key tradeoff is deliberate: publish fewer facts rather than risk attaching another entity's facts to the requested company.

## Implementation details

The command accepts a JSON array, one JSON object with organisation_number, JSONL objects, or one number per text line. It validates the Norwegian organisation number before making requests. The documented command is:

    python signalpost_agent.py --input smoke.json --output outputs/smoke.jsonl --state outputs/state.sqlite3 --timeout 3

For an official run, replace smoke.json with Builderr's supplied batch. The command writes one JSONL envelope per input, a machine-readable run report and an HTML profile browser. SQLite holds content-addressed snapshots and current pointers. Python 3.10+ standard library is the only dependency; there is no model key or paid search API.

BRREG is the identity source. Entity details and registered roles are retrieved from its official APIs. Financial key figures use BRREG's separate public annual-account API and are only accepted when the returned organisation number matches the requested company. This key-figures service is a preview API; availability and coverage are limited.

Website retrieval is limited to the HTTPS hostname explicitly registered by BRREG. The code checks robots.txt, resolves public IP addresses, rejects unsafe redirects, bounds the response and publishes descriptive facts only if the page contains the exact legal name or the organisation number. A careers link is not treated as proof of an active job.

Each result includes the requested identity, claims/statuses, evidence references, retrieval dates and reporting periods where available, source snapshots, errors, change details and run/request/cost metadata. Seven claim families each get one terminal status: available, not_available, blocked, not_applicable, ambiguous or failed. Per-company errors are contained so one lookup failure does not silently drop the rest of the batch. A deterministic summary uses supported registry facts and lists unknown families. Identical snapshots do not create false changes; changed snapshots retain history and list changed fields.

The baseline does not discover or verify current job postings, parse account PDFs, gather dated news or activity, crawl beyond the one declared page, use general web search, or scrape social platforms. It has no LLM stage. These lanes were excluded because the evaluation contract, permitted source conditions, service credentials and live resource budget had not been verified; an unmeasured complex collector could add unsupported matches and reduce reproducibility.

## Verification performed

The current local test run on 17 October 2026:

    python -m unittest -v
    Ran 10 tests in 2.417s
    OK

The suite covers invalid-number rejection before network, BRREG field/evidence mapping, finance extraction and cross-entity rejection, exact website identity checks, private-address rejection, ambiguous/mismatched sites, repeat-run idempotency, change detection, safe HTML escaping, and a mocked 100-input batch with one complete terminal envelope per input.

The mocked 100-company test proves envelope and batch-handling behavior under fixtures; it does not measure live-company coverage, accuracy, uptime or competition score. An earlier live smoke attempt with five sample numbers returned five terminal failure envelopes because DNS resolution for BRREG was unavailable in this environment. It recorded five requests and 206 ms aggregate request time; paid API cost was $0.00. This validates failure reporting, not company data, and is not the required live 100-company run.

Paid third-party API/model cost is $0 per run in this implementation. Live 100-company compute and wall time have not been measured. If a model/search lane is added, measure and declare its cost and run time under the evaluator limits.

## Current competition fit

The live official Builderr page, checked 17 October 2026, states weights of 50 recall/coverage, 30 precision/evidence, 12 synthesis and 8 UX. A previous listing excerpt in the conversation showed 35/30/20/10/5; use the currently published Builderr page for the current rubric. Qualification is at least 65/100 on an official run, with no separate category thresholds.

Builderr asks for a 100-company smoke-test result or report, repository URL, exact commit, one run command, model/API/licence details, expected official-run cost and contact details. The live page says testing at 1,000+ profiles is allowed, but precomputed profiles and a submitted company manifest are not required: Builderr supplies fresh company batches. Every official input must yield one result within the time/resource budget. The live page lists the challenge close as 21 October 2026. The earlier Unstop excerpt in the conversation had a 17 October project-link deadline and separate email instructions; verify the entry channel and applicable deadline before submission.

The downloadable evaluation contract at https://builderr.ai/docs/signalpost-evaluation-harness.md is linked by the official page but could not be fetched here because the browser fetch route rejects its Markdown content type. The starter brief and permitted-source Markdown links failed for the same reason. Exact envelope field compatibility, full evaluator behavior and permitted-source interpretation therefore remain unverified. The live leaderboard page reviewed 17 October showed 43 scored submissions and zero qualified at that point; that is context, not this project's score.

| Requirement | State | Evidence |
|---|---|---|
| Accept a supplied company number and reject malformed input | Implemented | CLI and offline tests |
| Exact company identity and evidence links | Implemented conservatively | BRREG anchor, financial number check, website name/number gate |
| Preserve refresh history and detect changes | Implemented | SQLite snapshot tests |
| Return one result per input | Fixture-verified | Mocked 100-company batch; live batch not run |
| Official 100-company report | Outstanding | Only five live attempts; DNS unavailable |
| Exact evaluator schema | Unverified | Contract unavailable to this environment |
| 1,000 precomputed profiles | Not required by the current page | Builderr supplies official batches |
| Official score and qualification | Unknown | No official evaluation |
| Contact information | Outstanding | Must be supplied by project owner before entry |

## GitHub update and correction

The GitHub API integration could read the repository but rejected file writes with HTTP 403. The workspace's shell also could not resolve github.com, and there was no authenticated Git CLI. The project was therefore uploaded through the authenticated GitHub browser UI. The resulting commit is dcf649deabd41d5f8e0f56d5304e9a2fbbdcdf06, titled “Replace repository files with completed Signalpost project.” GitHub's main branch listing and the uploaded README, agent and report were checked after the commit.

An earlier version of ENGINEERING_REPORT.md uploaded with the source still says that the repository remained private and that the revised version was not pushed. That became false after the upload. This completion report corrects the current state: the repository is public and the implementation is pushed. The previous Git history was not deleted.

## Next steps, in order

1. Obtain the exact evaluation contract and real 100-company smoke input from Builderr. Verify fields, input format, permitted sources, time/resource limits and evaluator launch environment.
2. Clone the exact pinned commit to a clean machine with working network and run the documented command against the actual 100 companies.
3. Record the input/batch identifier, commit hash, command, output count, run time, errors, requests, source families, cost and representative entity/evidence review. Confirm one terminal envelope per input and zero dropped rows.
4. Fix schema or live reliability failures before adding more sources. Add contact and confirm whether the Unstop deadline, Builderr cutoff or both apply. Prepare the exact repository link, commit, run command, source/API/licence and cost details.
5. Once baseline behavior is measured, test permitted job and dated-activity sources in controlled comparisons. Add LLM extraction only if it increases supported recall without increasing wrong-company publication or exceeding budget.
6. Submit a pinned commit and real smoke report. Claim qualification only after Builderr supplies an official score.

## Sources

- Builderr challenge, requirements and current rubric: https://builderr.ai/challenges/signalpost
- Builderr evaluation contract (linked, retrieval blocked): https://builderr.ai/docs/signalpost-evaluation-harness.md
- BRREG data/API licence: https://www.brreg.no/en/use-of-data-from-the-bronnoysund-register-centre/datasets-and-api/
- BRREG annual-account key-figures API specification: https://raw.githubusercontent.com/brreg/regnskapsregister-api/main/src/main/resources/specification/regnskapsregister.json
- Project repository: https://github.com/AnanthKumar1810/signalpost-agent
