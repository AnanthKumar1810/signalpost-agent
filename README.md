# Signalpost company intelligence agent

This is a runnable, evidence-first baseline for researching Norwegian companies by organisation number. It anchors each profile in the Brønnøysund Register Centre (BRREG), records source snapshots and retrieval times, and returns one JSONL envelope per input. A failed or blocked source is reported explicitly; the agent never turns missing data into zero.

## Run it

Requires Python 3.10 or newer. The program uses only the Python standard library and makes no paid model or search API calls.

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/smoke-100-final.jsonl --state outputs/smoke-100-final.sqlite3 --timeout 2 --workers 4
```

For the best no-key website discovery, add `--wikidata-websites`. This makes one
batched Wikidata lookup per 50 verified company numbers that lack a BRREG website.
Wikidata's exact Norwegian organisation-number match is only a lead: the candidate
homepage must still pass this agent's HTTPS, public-address, robots, and exact
company-identity checks before facts are published. The option uses no paid API.
If Wikidata is unreachable, the run records a discovery failure and still emits one
result envelope per input.

To add NAV's opt-in public vacancy feed, append `--nav-jobs`. The default scan covers up to 180 days of feed changes (NAV postings may remain active for up to six months), with a 25-page and 100-detail budget. Tune those limits with `--nav-days`, `--nav-pages`, and `--nav-details`; the feed may still report truncation and is not a complete jobs index. It makes no paid API calls.

When a verified company website explicitly links to a supported Greenhouse, Lever, or Ashby job board, the agent can query that provider's public job API. Each vacancy remains linked to both the verified company-site link and the canonical job-board record. This is bounded to three boards and 30 postings per board. The profile browser supports search, filtering, coverage sorting, source links and retrieval dates, and comparison of up to three companies. It is a local review artifact; it is not a hosted service.

Replace the example input with Builderr's supplied JSON or JSONL batch. `--workers` accepts 1–16 and defaults to four. The command also writes a JSON run report and an HTML profile browser beside the output. SQLite stores source hashes and prior snapshots so repeated content is idempotent and source changes remain visible.

The checked-in 100-company input comes from Builderr's public eligible-company universe. The corresponding live output and report document one test run; they are examples for local review, not the official competition run. See [the live judge report](SIGNALPOST_JUDGE_REPORT.md) and [the engineering report](ENGINEERING_REPORT.md).

## Optional candidate discovery and summaries

`--brave-websites` can extend `--wikidata-websites` when BRREG has no website and the exact-number Wikidata lookup finds no candidate. It searches by organisation number first and legal name only if the first query returns no candidates. Search results are leads only: the normal HTTPS, safe-address, robots and company-identity checks must accept a candidate before any website facts are published. Brave titles and snippets are discarded. Enable this only with a Brave plan that grants storage rights, because the accepted URL is retained as evidence; set `SIGNALPOST_BRAVE_STORAGE_RIGHTS=1` to acknowledge that condition. The default limit is 600 requests, with a hard maximum of 2,000. At the published rate of $5 per 1,000 requests, that is an estimated $3 maximum for 600 successful requests; actual provider billing is not recorded by this agent. The feature makes no calls unless explicitly enabled.

`--llm-summary` optionally sends a JSON object containing the verified identity anchor, the collected claim fields (`family`, `status`, `facts`, `reason`), and explicit changes to the configured OpenAI model. It does not send raw pages, search results, or evidence excerpts. The model receives the plain-text summary prompt in `llm_summary.py`; output is rejected when it is not plain prose, has the wrong sentence count, omits the missing-category sentence, or contains an unsupported number. Rejected or failed output leaves the deterministic evidence-based summary in place. Calls require `OPENAI_API_KEY` and are never made in the default run. Text meaning cannot be fully proven by format checks, so review provenance in the retained local fact map before relying on a generated summary. API usage is reported from provider token counts when available; it is an estimate, not a billing statement.

The [sample dashboard](https://ananthkumar1810.github.io/signalpost-agent/) is a frozen, mobile-responsive browser view of the October 19 practice sample. It is not live company data or an official competition score.

## Evidence and source policy

BRREG's Enhetsregisteret API supplies legal identity, registered address, industry, registered website and roles. Its open data is licensed under NLOD 2.0. Accounts are requested from BRREG's production endpoint at `data.brreg.no/regnskapsregisteret/regnskap`; the PPE hostname is reserved for pre-production and is not used. BRREG describes the accounts API as a preview with limited coverage and no service guarantee. A source outage is reported as failed, and no financial value is estimated or fabricated.

See [FETCH_FAILURE_ANALYSIS.md](FETCH_FAILURE_ANALYSIS.md) for the measured failure causes, endpoint correction, live-verification status and ordered recovery plan.

See [REFERENCE_EVALUATION.md](REFERENCE_EVALUATION.md) for the five isolated reference-project revisions, checks actually run, environment blockers and integration decisions.

A registered or exact-organisation-number-discovered candidate website is fetched only over HTTPS after checking its `robots.txt`; private-address targets and cross-domain redirects are rejected. Website identity accepts an exact organisation number or a distinctive full registered name in page text, including a conservative suffix-free form. A single weak brand-name mention is not enough. The agent does not scrape restricted social platforms; it records only social profile links explicitly linked from a verified company site. It checks same-domain job and dated news/press pages, RSS/Atom feeds advertised by that site, and explicitly linked public Greenhouse, Lever or Ashby boards. Feed items require a date and same-company-host article link. Optional LLM search results stay candidate-only unless their cited page, exact company identity, quote and date pass source checks.

Each published fact points to a content-hashed source snapshot and includes a retrieval time. A final integrity gate drops facts unless their evidence record and source snapshot agree on organisation number, source family and field. Financial records are additionally checked against the requested organisation number. The deterministic summary uses only sourced registry facts and lists unknown families. The evidence contains source links and excerpts; HTML output escapes untrusted content. Idempotent GET requests get one short retry after transient transport errors or HTTP 500/502/503/504; retries count toward the outbound-request total, and company-specific 404/403 responses are not retried.

## Checks

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
python -m py_compile signalpost_agent.py nav_jobs.py public_ats.py wikidata_websites.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py test_wikidata_websites.py test_public_ats.py
```

The current local suite passes 94 tests, including RSS/Atom evidence, NAV entity checks, conservative full-page company-name matching, exact-ID Wikidata discovery fixtures, linked public ATS-board evidence, Brave candidate-query handling, and summary-output safeguards. A fresh combined live 100-company run previously returned 100 complete profiles and verified identities in 177.3 seconds with 348 requests; 99 finance lookups returned data and one returned HTTP 500. It found 1 website, 1 jobs result, and 1 public-activity result. NAV found two candidates and matched zero; Wikidata checked 92 companies without a registered website and returned no candidates. These source changes did not increase coverage in that sample. No fresh live 100-company run was possible in this network-restricted environment, and no Brave or OpenAI credentials were configured, so the new optional APIs have fixture tests only. The historical 108.7-second run is a separate baseline. See [the judge report](SIGNALPOST_JUDGE_REPORT.md), [the coverage-improvement report](COVERAGE_IMPROVEMENTS.md), and [the independent QA report](QA_TEST_REPORT.md) for exact results and limitations.

## Readiness

This package is executable and reviewable, but it is not a demonstrated qualifying entry. The current implementation favors exact attribution and safe abstention; its live run has very low external-information coverage, and the official hidden-set score cannot be calculated locally. See the judge report for a rubric-based estimate and the work still needed before qualification.



The public repository is [AnanthKumar1810/signalpost-agent](https://github.com/AnanthKumar1810/signalpost-agent). Check the submission manifest for the exact pushed revision and the distinction between repository publication and a Builderr submission.
