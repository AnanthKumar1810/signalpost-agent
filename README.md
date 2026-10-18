# Signalpost company intelligence agent

This is a runnable, evidence-first baseline for researching Norwegian companies by organisation number. It anchors each profile in the Brønnøysund Register Centre (BRREG), records source snapshots and retrieval times, and returns one JSONL envelope per input. A failed or blocked source is reported explicitly; the agent never turns missing data into zero.

## Run it

Requires Python 3.10 or newer. The program uses only the Python standard library and makes no paid model or search API calls.

```powershell
python signalpost_agent.py --input smoke_100.json --output outputs/smoke-100-final.jsonl --state outputs/smoke-100-final.sqlite3 --timeout 2 --workers 4
```

To add NAV's opt-in public vacancy feed, append `--nav-jobs`. The NAV scan is bounded to recent feed updates and a fixed page/detail budget; it can report truncation and is not a complete jobs index. It makes no paid API calls.

Replace the example input with Builderr's supplied JSON or JSONL batch. `--workers` accepts 1–16 and defaults to four. The command also writes a JSON run report and an HTML profile browser beside the output. SQLite stores source hashes and prior snapshots so repeated content is idempotent and source changes remain visible.

The checked-in 100-company input comes from Builderr's public eligible-company universe. The corresponding live output and report document one test run; they are examples for local review, not the official competition run. See [the live judge report](SIGNALPOST_JUDGE_REPORT.md) and [the engineering report](ENGINEERING_REPORT.md).

## Evidence and source policy

BRREG's Enhetsregisteret API supplies legal identity, registered address, industry, registered website and roles. Its open data is licensed under NLOD 2.0. Accounts are requested from BRREG's production endpoint at `data.brreg.no/regnskapsregisteret/regnskap`; the PPE hostname is reserved for pre-production and is not used. BRREG describes the accounts API as a preview with limited coverage and no service guarantee. A source outage is reported as failed, and no financial value is estimated or fabricated.

See [FETCH_FAILURE_ANALYSIS.md](FETCH_FAILURE_ANALYSIS.md) for the measured failure causes, endpoint correction, live-verification status and ordered recovery plan.

See [REFERENCE_EVALUATION.md](REFERENCE_EVALUATION.md) for the five isolated reference-project revisions, checks actually run, environment blockers and integration decisions.

A registered website is fetched only over HTTPS after checking its `robots.txt`; private-address targets and cross-domain redirects are rejected. Website claims require the exact legal name or organisation number on the page. The agent does not scrape restricted social platforms. It checks a small number of same-domain job and dated news/press links, plus RSS/Atom feeds explicitly advertised by that registered site. Feed items require a date and a same-company-host article link. Coverage remains limited; unsupported or inaccessible sources stay explicitly unavailable or failed rather than guessed. Optional LLM search results stay candidate-only unless their cited page, exact company identity, quote and date pass source checks.

Each published fact points to a content-hashed source snapshot and includes a retrieval time. A final integrity gate drops facts unless their evidence record and source snapshot agree on organisation number, source family and field. Financial records are additionally checked against the requested organisation number. The deterministic summary uses only sourced registry facts and lists unknown families. The evidence contains source links and excerpts; HTML output escapes untrusted content. Idempotent GET requests get one short retry after transient transport errors or HTTP 500/502/503/504; retries count toward the outbound-request total, and company-specific 404/403 responses are not retried.

## Checks

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
python -m py_compile signalpost_agent.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py
```

The local suite passes 71 tests, including RSS/Atom evidence and NAV entity checks. The final live 100-company run returned 100 complete profiles in 108.7 seconds with 322 requests; 99 finance lookups returned data and one returned HTTP 500. It found 1 jobs profile and 1 public-activity profile; the latter contains 10 dated items from a registered company's own feed and passed the evidence-integrity gate. NAV added zero matches and hit its configured page cap. Compared with the earlier 100-company run, public activity increased from zero to one available profile; jobs coverage did not change. See [the coverage-improvement report](COVERAGE_IMPROVEMENTS.md) and [the independent QA report](QA_TEST_REPORT.md) for exact results. The Builderr starter kit's refresh replay passed locally with 2/2 expected changes, 0 false positives, 0 false negatives and an idempotent rerun. Its broader test suite could not be run here because the optional `extruct` dependency is not installed.

## Readiness

This package is executable and reviewable, but it is not a demonstrated qualifying entry. The current implementation favors exact attribution and safe abstention; its live run has very low external-information coverage, and the official hidden-set score cannot be calculated locally. See the judge report for a rubric-based estimate and the work still needed before qualification.



The public repository is [AnanthKumar1810/signalpost-agent](https://github.com/AnanthKumar1810/signalpost-agent). Check the submission manifest for the exact pushed revision and the distinction between repository publication and a Builderr submission.
