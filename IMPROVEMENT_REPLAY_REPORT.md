# Signalpost improvement and controlled replay — 27 October 2026

## Decision

Karthik's [public Signalpost source](https://github.com/JatothKarthikKumar/signalpost-company-research-agent) reinforced the value of a small official-register core, bounded work, and deterministic output. Our agent already had those properties and stricter identity/evidence checks. An isolated review of that public code found unchecked identity, jobs, and account-order behavior; it did not provide a safe website-discovery technique to copy. I therefore expanded facts from the exact-number BRREG record already fetched by our agent. No reference source code was copied.

The official [Builderr rubric](https://builderr.ai/challenges/signalpost) gives 50/100 points to recall, 30 to precision, 12 to synthesis and 8 to UX. More sourced fields may help recall, but this replay does not measure Builderr's checked collection or assign an official score.

## Implemented

`signalpost_agent.py` now emits additional BRREG-supplied company-detail facts when present: legal-form description; incorporation and business-register dates; stated purpose; institutional sector code and description; VAT-registration status; latest submitted accounts year; NOK share capital and share count; public registry email, phone and mobile; and mailing address. Share capital is emitted only when BRREG explicitly calls it `Aksjekapital`, says the currency is NOK, and supplies a finite nonnegative amount. The associated BRREG capital date is retained. All new facts use the existing exact-organisation-number registry snapshot, its retrieval timestamp, and the final evidence-integrity gate. Mailing address also appears in the locations family. The enrichment makes no extra HTTP request and no model call.

## Measured results

| Check | Before | After | Interpretation |
| --- | ---: | ---: | --- |
| Saved BRREG responses replayed | 100 | 100 | Same 100 company inputs and saved official response bodies from the October 22 practice run. |
| Verified identities | 100 | 100 | Exact response-number check still passed. |
| Company-detail fact occurrences | 1,012 | 2,062 | **1,050 additional sourced field occurrences**, with no previous field removed. This is not 1,050 unique companies or a score. |
| Network requests in the replay | 0 | 0 | Responses came from local saved snapshots. |
| Full local tests | 135 passed | 136 passed | One regression covers new facts, dates, evidence and mailing-address location status. |

The replay loaded the original `signalpost_agent.py` from Git revision `4622c4f` and the revised working file, supplied each with the same saved registry/roles/accounts payloads, and disabled website fetching. The added occurrences included stated purpose in 94 profiles, incorporation date in 96, sector code in 98, NOK share capital in 89, contact email in 30, and mailing address in 21. These are *field occurrence counts*, not manually verified semantic recall against Builderr's reference collection.

A separate live run for organisation `822021972` after the change returned one verified identity, 22 company-detail facts and 44 facts across all available families, using three BRREG requests. `python audit_profiles.py outputs/benchmark-registry-one-live.jsonl` found **zero lineage violations** and no recorded errors. Its adjacent `.report.json` records the request count and terminal family states. The live run did not fetch a website because it was testing the official-data path with `--no-website`.

## Search experiment rejected

I also tested an optional search adjustment: visit a search hit's same-host homepage and run a legal-name search when organisation-number results look like directory listings. The sample revealed why current external coverage is low: number searches for several companies returned third-party company directories rather than company-owned domains. But the proposed adjustment did not turn those leads into verified sources.

| Live check | Verified web / jobs / activity | Requests | Result |
| --- | --- | ---: | --- |
| Earlier saved 10-company slice | 1 / 1 / 0 | Different full-run budget | Comparison reference only; source timing and enabled options differed. |
| Homepage experiment, 10 companies | 1 / 1 / 0 | 120, cap exhausted | No additional verified site. |
| Directory-filter and name-search experiment, same 10 | 1 / 1 / 0 | 160, cap exhausted | 21 candidate URLs, zero verified candidates; 378.7 seconds and 14 Tavily credits reported. |

The search experiments were **reverted from the deliverable**. Keeping them would consume more of Builderr's request/time budget without measured coverage gain. Tavily and TinyFish remain optional as before, and their search results remain leads rather than evidence. A one-company diagnostic for `822021972` also made two Tavily calls and found no verifiable website.

## Failures and limits

- The first 10-company pilot could not resolve `data.brreg.no` in the restricted shell; it returned ten failed identities. The separate network-enabled run succeeded. The DNS-failed run was excluded from the coverage comparison.
- The first command for the one-company validation used a 120-second deadline with the runner's default 120-second finalization reserve, so argument validation rejected it. The rerun explicitly set a 20-second reserve and completed.
- The search pilots reached their outbound-request caps. The run-wide budget recorded 120/120 and 160/160 respectively; `profile_reported_requests` exceeded those caps by five in both reports because it includes attempted website calls denied by the budget. The run-wide `requests` figure is the count to use for actual permitted outbound calls.
- More BRREG fields increase demonstrated *source-field coverage* only. Website, active-job, and public-activity coverage did not improve in the 10-company experiment. No current official Builderr score or qualification can be claimed from these checks.

## Reproduce

```powershell
python -m unittest discover -s . -p 'test_*.py' -q
python audit_profiles.py outputs/benchmark-registry-one-live.jsonl
```

The live artifact and its report are local development outputs under `outputs/`; the repository keeps the implementation, regression test, and this measured report. An official Builderr run is still needed to know whether the added fields raise the score.
