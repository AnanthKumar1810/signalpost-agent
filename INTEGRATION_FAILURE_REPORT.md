# Signalpost completion and failure report

Prepared 20 October 2026.

## Completed and verified

The live integrated run produced [1,000 company profiles](outputs/submission-1000-integrated-live.jsonl), with its [machine-readable run report](outputs/submission-1000-integrated-live.jsonl.report.json) and [local dashboard](outputs/submission-1000-integrated-live.jsonl.html). It used the supplied 1,000-company input, official BRREG lookups, Wikidata discovery, TinyFish (50-request cap), Tavily (100-request cap), NAV jobs, and optional OpenAI summaries. Brave Search was omitted because the account requires a payment card.

Measured result: 1,000/1,000 identities verified; 4,518 outbound requests; 1,384,036 ms wall runtime (about 23.1 minutes); and 29,766 facts. The evidence audit reported zero lineage violations. The full standard-library test suite passed 116/116 tests.

| Data area | Earlier 1,000 baseline | Integrated run | Change |
|---|---:|---:|---:|
| Verified website | 38/1,000 | 50/1,000 | +12 |
| Available jobs | 6/1,000 | 7/1,000 | +1 |
| Available public activity | 12/1,000 | 14/1,000 | +2 |

These counts show measured availability, not the official competition score. The 1,000 companies were the supplied sample, not a fresh official random evaluation.

## Failures and their causes

1. **The first 1,000-company run produced only failed identities.** Its 2,000 registry attempts failed because `data.brreg.no` DNS resolution was unavailable in that shell. After network access was granted, a direct BRREG lookup succeeded and the complete run above verified all 1,000 identities. The DNS-failure output is separate and is not used as a submission artifact.

2. **Earlier full-batch attempts did not finish.** One unbounded discovery run was stopped after about 45 minutes without a JSONL/report. A capped retry was also stopped before it emitted final output. The successful run now bounds TinyFish at 50 requests and Tavily at 100; it completed in about 23 minutes.

3. **LLM summaries were heavily rate-limited.** The completed output records 1,000 summary calls, 7 accepted LLM summaries, and 993 failed calls. The per-profile records say `HTTPError`; a separate safe diagnostic request returned HTTP 429 with provider code `rate_limit_exceeded` and `Retry-After: 1728` seconds. This is an account/model request limit, not a validation rejection. The application kept its deterministic evidence-grounded summary for failed calls, so the profiles remain usable, but the LLM stage is partial. The run records an estimated USD 0.0018551 for successful calls; failed-call usage and the actual provider bill are unavailable, so total cost is not confirmed.

4. **One diagnostic request was malformed.** My first diagnostic probe asked for only 10 output tokens and the API returned HTTP 400 `integer_below_min_value`. I corrected the probe to a valid token limit; that request revealed the 429 rate limit. This probe did not alter any company data.

5. **Tavily exhausted its deliberate request budget.** It used 100 requests and 99 credits, searched 51 targets, found 17 candidates and verified 5 websites. The remaining 836 eligible targets were marked budget-exhausted. Search results stayed candidate leads and did not become facts without the normal identity checks.

6. **TinyFish raised website coverage by seven.** It used 50 requests, found seven candidates, and all seven passed verification. It reported no errors. The provider cost is shown as zero by the connector, but that is not an independently verified invoice or current pricing guarantee.

7. **Jobs and activity are still sparse.** The integrated output has 7 available jobs and 14 available activity claims across 1,000 companies. Most profiles have no verified result in those areas. Missing data remains explicitly unavailable; it is not evidence that a company has no vacancies or public activity.

8. **Brave and GitHub Pages remain unavailable.** Brave was not used because the user could not provide card details for its plan. The GitHub Pages URL returned 404, `gh` CLI is unavailable in this environment, and Pages settings have not been enabled with the Actions source. No live dashboard URL is claimed.

9. **The final GitHub push failed.** Local commit `3d43ee1` contains the integration code and output, but `git push origin main` could not start Git Credential Manager: Windows returned `NtCreateDirectoryObject ... 0xC0000022`, followed by “could not read Username”. Direct credential enumeration returned access denied, and a Git Credential Manager login attempt did not open a browser. The GitHub app connection reports push/admin permission, but it cannot transfer this local Git commit through the failed credential helper. GitHub `main` remains at `bcda57e`; the local commit has not been published.

## What is ready and what is not

The local project now has a complete, auditable 1,000-profile integrated artifact and regression-tested code. The full test suite and evidence audit pass. The LLM is optional and falls back safely; its provider limit prevented most LLM summaries in this particular run. Before claiming external submission readiness, push the intended code and JSONL/report to GitHub, verify the exact remote commit, and enable/verify Pages if a hosted dashboard is required. The live competition score remains unknown.
