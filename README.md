# Signalpost company intelligence agent

This is a runnable, evidence-first baseline for researching Norwegian companies by organisation number. It anchors each profile in the Brønnøysund Register Centre (BRREG), records source snapshots and retrieval times, and returns one JSONL envelope per input. A failed or blocked source is reported explicitly; the agent never turns missing data into zero.

## Run it

Requires Python 3.10 or newer. The program uses only the Python standard library and makes no paid model or search API calls.

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites
```

The included `--wikidata-websites` performs one
batched Wikidata lookup per 50 verified company numbers that lack a verified website.
Wikidata's exact Norwegian organisation-number match is only a lead: the candidate
homepage must still pass this agent's HTTPS, public-address, robots, and exact
company-identity checks before facts are published. The option uses no paid API.
If Wikidata is unreachable, the run records a discovery failure and still emits one
result envelope per input.

The included `--nav-jobs` scans up to 180 days of feed changes (NAV postings may remain active for up to six months), with a 25-page and 100-detail budget. Tune those limits with `--nav-days`, `--nav-pages`, and `--nav-details`; the feed may still report truncation and is not a complete jobs index. It makes no paid API calls.

When a verified company website explicitly links to a supported Greenhouse, Lever, or Ashby job board, the agent can query that provider's public job API. Each vacancy remains linked to both the verified company-site link and the canonical job-board record. This is bounded to three boards and 30 postings per board. The profile browser supports search, filtering, coverage sorting, source links and retrieval dates, and comparison of up to three companies. It is a local review artifact; it is not a hosted service.

Replace the example input with Builderr's supplied JSON or JSONL batch. `--workers` accepts 1–16 and defaults to four. The command also writes a JSON run report and an HTML profile browser beside the output. SQLite stores source hashes and prior snapshots so repeated content is idempotent and source changes remain visible.

The checked-in 100- and 1,000-company inputs come from Builderr's public eligible-company universe. Their live outputs and reports are practice runs, not the official competition test. See [the judge report](SIGNALPOST_JUDGE_REPORT.md) and [the final technical report](FINAL_TECHNICAL_REPORT.md).

## Optional candidate discovery and summaries

`--tinyfish-websites` adds bounded TinyFish Search discovery after BRREG and Wikidata. It requires `TINYFISH_API_KEY`, searches the exact organisation number before a legal-name fallback, and retains no result title or snippet. Directory, social and private-address results are rejected; domain similarity only ranks candidates and does not discard a possible trading-name or group-brand site. A remaining candidate must still pass HTTPS, robots and exact-entity checks. The live development key used for testing is not stored in this repository. Builderr says evaluator runs cannot depend on a credential tied to a participant's external account, so this option is **not** in the recommended evaluator command unless Builderr supplies a reproducible server-side key.

`--brave-websites` can extend `--wikidata-websites` when BRREG has no website and the exact-number Wikidata lookup finds no candidate. It searches by organisation number first and legal name only if the first query returns no candidates. Search results are leads only: the normal HTTPS, safe-address, robots and company-identity checks must accept a candidate before any website facts are published. Brave titles and snippets are discarded. Enable this only with a Brave plan that grants storage rights, because the accepted URL is retained as evidence; set `SIGNALPOST_BRAVE_STORAGE_RIGHTS=1` to acknowledge that condition. The default limit is 600 requests, with a hard maximum of 2,000. At the published rate of $5 per 1,000 requests, that is an estimated $3 maximum for 600 successful requests; actual provider billing is not recorded by this agent. The feature makes no calls unless explicitly enabled.

`--tavily-websites` is an optional no-card search alternative when Brave is unavailable. It requires `TAVILY_API_KEY`, searches the exact organisation number and then legal name, and returns only candidate URLs; it does not use search answers, snippets or raw page text. Each URL must pass the same website ownership, HTTPS, robots and entity checks before publication. The default hard limit is 600 API requests per batch. Tavily's current free plan advertises 1,000 monthly API credits without a card; this run records the provider-reported credit usage and does not upgrade or charge automatically. Check the provider's current [pricing](https://www.tavily.com/pricing) and [Search API reference](https://docs.tavily.com/documentation/api-reference/endpoint/search) before use, as limits and terms may change.

`--llm-summary` optionally sends a JSON object containing the verified identity anchor, the collected claim fields (`family`, `status`, `facts`, `reason`), and explicit changes to the configured OpenAI model. It does not send raw pages, search results, or evidence excerpts. Summaries run after all enrichment and evidence checks. Output is rejected when it is not plain prose, has the wrong sentence count, omits the missing-category sentence, contains an unsupported number, or has a factual sentence with no matching fact value. The retained claim map ties each sentence to the specific supporting fields. Rejected or failed output leaves the deterministic evidence-based summary in place. Calls require `OPENAI_API_KEY` and are never made in the default run. Token overlap is a conservative filter, not a proof of semantic entailment; review the claim map. A 429 response preserves `Retry-After` and opens a circuit breaker for the rest of that run. API usage is reported from provider token counts when available; it is an estimate, not a billing statement.

The runner checkpoints each completed company profile beside the JSONL output. If a run reaches its collection deadline or is interrupted, use `--resume` with the same input, output and state paths to continue the unfinished company fetches. The default batch deadline is 40 minutes, with 2 minutes reserved for final output and dashboard generation. `--max-outbound-requests` defaults to 2,000 and is enforced across BRREG, websites, search providers, NAV and optional model calls, not as separate provider-only limits. The run report includes consumed budget, deadline, resumed count and checkpoint location. The optional `--web-search-judge` switch is rejected because it only produces candidate-level data rather than scored, evidence-backed claims; it is excluded from the entry pipeline.

The frozen, mobile-responsive sample dashboard is in [`docs/index.html`](docs/index.html). It shows the October 19 practice sample, not live company data or an official competition score. To publish it, enable **Settings → Pages → Build and deployment → GitHub Actions** for this repository. The workflow skips cleanly until that setting is enabled; after a successful run the site will appear at [ananthkumar1810.github.io/signalpost-agent](https://ananthkumar1810.github.io/signalpost-agent/).

## Evidence and source policy

BRREG's Enhetsregisteret API supplies legal identity, registered address, industry, registered website and roles. Its open data is licensed under NLOD 2.0. Accounts are requested from BRREG's production endpoint at `data.brreg.no/regnskapsregisteret/regnskap`; the PPE hostname is reserved for pre-production and is not used. BRREG describes the accounts API as a preview with limited coverage and no service guarantee. A source outage is reported as failed, and no financial value is estimated or fabricated.

See [FETCH_FAILURE_ANALYSIS.md](FETCH_FAILURE_ANALYSIS.md) for the measured failure causes, endpoint correction, live-verification status and ordered recovery plan.

The repeated BRREG HTTP 500 for entity `987441623` is diagnosed separately in [FINANCIAL_987441623_DIAGNOSIS.md](FINANCIAL_987441623_DIAGNOSIS.md). The code marks only BRREG's explicit unsupported `IDEELL` accounting-plan response as `not_applicable`; generic 500 errors remain failures.

See [REFERENCE_EVALUATION.md](REFERENCE_EVALUATION.md) for the five isolated reference-project revisions, checks actually run, environment blockers and integration decisions.

A registered or exact-organisation-number-discovered candidate website is fetched only over HTTPS after checking its `robots.txt`; a registered HTTP URL is upgraded to HTTPS first and private-address targets are rejected. Known business-information directories, marketplaces and social platforms are never published as the company's own website, even if they display its name or number. Other search candidates are leads only and require corroboration from a same-host legal/contact/about page; a nine-digit string is accepted as an organisation number only when explicitly labelled as one. Untrusted Wikidata and search candidates cannot redirect across hosts. A BRREG-listed legacy domain may redirect to a different public HTTPS host; plaintext redirect hops are upgraded before any request, public-address checks are repeated for the destination, and the final page must still pass the company-identity gate. For a domain supplied directly by the matching BRREG record, the page may identify itself using the exact registered name with only its AS/ASA legal-form suffix omitted; for open-web candidates, that shortcut is not allowed. A suffix-free brand or one-word match alone is not proof for an unregistered candidate. If identity is not visible on the homepage, it checks up to three same-host about/contact/legal pages and cites the identity page alongside the home page. It does not scrape restricted social platforms; it records only social profile links explicitly linked from a verified company site. It checks same-domain job and dated news/press pages, bounded detail links, matching-entity JSON-LD, RSS/Atom feeds advertised by that site, and explicitly linked public Greenhouse, Lever or Ashby boards. Job and activity JSON-LD is used only when the listed hiring/publisher entity matches the organisation number or full registered name. Optional LLM search results stay candidate-only unless their cited page, exact company identity, quote and date pass source checks.

Each published fact points to a source-specific, content-hashed snapshot and includes a retrieval time. The SQLite store keys current snapshots by organisation, family and canonical source URL; the JSON payload preserves the extracted content used to reconstruct each fact, while a database migration retains pre-existing history. A final integrity gate drops facts unless their evidence record and source snapshot agree on organisation number, source family and field. Financial records are additionally checked against the requested organisation number. The deterministic summary uses only sourced registry facts and lists unknown families. The evidence contains source links and excerpts; HTML output escapes untrusted content. Idempotent GET requests get one short retry after transient transport errors or HTTP 500/502/503/504; retries count toward the outbound-request total, and company-specific 404/403 responses are not retried.

## Checks

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
python -m py_compile signalpost_agent.py nav_jobs.py public_ats.py wikidata_websites.py tinyfish_websites.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py test_wikidata_websites.py test_tinyfish_websites.py test_public_ats.py
```

The current local suite passes 130 tests, including regressions for source-level version history and database migration, LLM summary ordering and sentence-level provenance, registered-domain suffix-free exact-name matching, bounded legal-page identity, directory/marketplace-source rejection, JSON-LD company attribution, NAV trading-name candidates with exact-org verification, global request caps, resumable checkpoints, BRREG financial applicability and safe redirects. These fixture tests do not establish full-set live coverage, run time, cost, or a competition score. Earlier measured 100- and 1,000-company results predate this revision. A fresh 10-company BRREG-only run found one verified company site and its careers page, compared with zero in the stricter prior run; this is a small targeted improvement, not a projected competition score. A new controlled benchmark and official Builderr run are still required for a current score. See [the attached recommendations implementation report](ATTACHED_RECOMMENDATIONS_IMPLEMENTATION.md), [the final technical report](FINAL_TECHNICAL_REPORT.md), [the judge report](SIGNALPOST_JUDGE_REPORT.md), and [the independent QA report](QA_TEST_REPORT.md) for exact prior results and limitations.

## Readiness

This package is executable and reviewable, but it is not a demonstrated qualifying entry. Builderr's current official page says the local smoke test is not the competition score and qualification requires at least 65/100 on its official run. The implementation strongly protects exact attribution, while measured external-information coverage remains the main risk. See the final technical report for the measured comparisons and remaining limitations.



The public repository is [AnanthKumar1810/signalpost-agent](https://github.com/AnanthKumar1810/signalpost-agent). Check the submission manifest for the exact pushed revision and the distinction between repository publication and a Builderr submission.
