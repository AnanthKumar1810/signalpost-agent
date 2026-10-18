# Signalpost company intelligence agent

This is a runnable, evidence-first baseline for researching Norwegian companies by organisation number. It anchors each profile in the Brønnøysund Register Centre (BRREG), records source snapshots and retrieval times, and returns one JSONL envelope per input. A failed or blocked source is reported explicitly; the agent never turns missing data into zero.

## Run it

Requires Python 3.10 or newer. The program uses only the Python standard library and makes no paid model or search API calls.

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/smoke-100-final.jsonl --state outputs/smoke-100-final.sqlite3 --timeout 2 --workers 4
```

Replace the example input with Builderr's supplied JSON or JSONL batch. `--workers` accepts 1–16 and defaults to four. The command also writes a JSON run report and an HTML profile browser beside the output. SQLite stores source hashes and prior snapshots so repeated content is idempotent and source changes remain visible.

The checked-in 100-company input comes from Builderr's public eligible-company universe. The corresponding live output and report document one test run; they are examples for local review, not the official competition run. See [the live judge report](SIGNALPOST_JUDGE_REPORT.md) and [the engineering report](ENGINEERING_REPORT.md).

## Evidence and source policy

BRREG's Enhetsregisteret API supplies legal identity, registered address, industry, registered website and roles. Its open data is licensed under NLOD 2.0. Accounts are requested from BRREG's production endpoint at `data.brreg.no/regnskapsregisteret/regnskap`; the PPE hostname is reserved for pre-production and is not used. BRREG describes the accounts API as a preview with limited coverage and no service guarantee. A source outage is reported as failed, and no financial value is estimated or fabricated.

See [FETCH_FAILURE_ANALYSIS.md](FETCH_FAILURE_ANALYSIS.md) for the measured failure causes, endpoint correction, live-verification status and ordered recovery plan.

See [REFERENCE_EVALUATION.md](REFERENCE_EVALUATION.md) for the five isolated reference-project revisions, checks actually run, environment blockers and integration decisions.

A registered website is fetched only over HTTPS after checking its `robots.txt`; private-address targets and cross-domain redirects are rejected. Website claims require the exact legal name or organisation number on the page. The agent does not scrape restricted social platforms. It checks a small number of same-domain job and dated news/press links, so coverage is limited; unsupported or inaccessible sources remain explicitly unavailable or failed rather than guessed. Optional LLM search results stay candidate-only unless their cited page, exact company identity, quote and date pass source checks.

Each published fact points to a content-hashed source snapshot and includes a retrieval time. A final integrity gate drops facts unless their evidence record and source snapshot agree on organisation number, source family and field. Financial records are additionally checked against the requested organisation number. The deterministic summary uses only sourced registry facts and lists unknown families. The evidence contains source links and excerpts; HTML output escapes untrusted content. Idempotent GET requests get one short retry after transient transport errors or HTTP 500/502/503/504; retries count toward the outbound-request total, and company-specific 404/403 responses are not retried.

## Checks

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
python -m py_compile signalpost_agent.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py
```

The local suite passes 63 tests covering input validation, entity matching, evidence integrity, safe URL handling, bounded retry behavior, output completeness, idempotency, dashboard escaping, financial validation, optional judge safeguards, source-page citation verification and a mocked 1,000-company batch. A live 100-company BRREG run returned 100 complete profiles in 95.1 seconds with 314 requests; 99 finance lookups returned data and one returned HTTP 500. A fresh retry of that same company's finance endpoint still returned HTTP 500, so the finance claim remains failed. A live one-company check after the evidence-integrity gate preserved verified registry and role facts. See [the independent QA report](QA_TEST_REPORT.md) for exact results. The Builderr starter kit's refresh replay also passed locally with 2/2 expected changes, 0 false positives, 0 false negatives and an idempotent rerun. Its broader test suite could not be run here because the optional `extruct` dependency is not installed.

## Readiness

This package is executable and reviewable, but it is not a demonstrated qualifying entry. The current implementation favors exact attribution and safe abstention; its live run has very low external-information coverage, and the official hidden-set score cannot be calculated locally. See the judge report for a rubric-based estimate and the work still needed before qualification.



The public repository is [AnanthKumar1810/signalpost-agent](https://github.com/AnanthKumar1810/signalpost-agent). Check the submission manifest for the exact pushed revision and the distinction between repository publication and a Builderr submission.
