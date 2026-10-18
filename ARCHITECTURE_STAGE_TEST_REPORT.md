# Architecture stage and test report

**Run date:** 17 October 2026

## Isolated checks

| Stage | Result | Coverage |
|---|---|---|
| Input normalization | Passed | spaces and hyphens, invalid punctuation, ASCII-only digits and checksum errors |
| Batch input | Passed | JSON array, JSONL, plain text and duplicate preservation |
| Registry identity | Passed | BRREG response mapping, real 100-company seeded run and failure envelopes |
| Registered roles | Passed | nested BRREG person-name schema and real role responses |
| Accounts matching | Passed in fixtures; live confirmation pending | fixture rejects wrong-company values; historical run timed out because it used BRREG PPE; code now points at documented production host, but follow-up DNS failure prevents live confirmation |
| Website safety | Passed | HTTPS, robots, private-host rejection, same-host redirects and exact-entity checks |
| Active job heuristic | Passed | same-host page, title plus application action, evidence reference; 1 live result in seeded batch |
| Public activity | Not implemented | explicitly not available unless source check fails |
| Snapshot refresh | Passed | identical content is idempotent; changed fields are retained |
| Optional web-search candidate helper | Passed in mocked tests | malformed, weak-evidence and wrong-entity candidates rejected; no live key/API call |
| Batch runner and envelope | Passed | 100 inputs/100 envelopes, order preserved, four-worker live run, no internal errors |
| HTML profile browser | Passed | fixture and escaping tests |

## Integrated result

The archived run used a reservoir sample of 100 unique companies from all 411,160 rows of Builderr's public universe, with seed `20261017`. It returned 100 verified identities and 100 complete envelopes, with 1,599 evidence-linked facts, 313 requests, 175,881 ms wall time and USD 0.00 paid API cost. All finance requests went to PPE and timed out. Code now targets production; a fresh live check remains unverified because this session could not resolve either BRREG hostname. One website and one active job were verified; no dated activity was published. See `FETCH_FAILURE_ANALYSIS.md` for the evidence and recovery plan.

All **24 local tests passed** and the Python files compiled. The official starter-kit refresh replay found 2/2 expected changes, 0 false positives and 0 false negatives, with complete evidence and idempotent rerun. The starter kit's full external test suite was not run because `extruct` was unavailable.

The five third-party reference systems were reviewed for architecture, but were not all installed and independently benchmarked. This report does not claim they were.
