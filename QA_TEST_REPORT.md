# Independent QA report — initial findings and fix plan

**Review date:** 18 October 2026  
**Project inspected:** `outputs/signalpost-agent-v2` in the current workspace  
**Baseline runtime:** Python 3.13.15 on Windows 11; standard-library implementation.  
**Purpose of this initial section:** record independently reproduced issues and planned fixes before changing application code, as requested by the QA brief. The final report will add the post-fix suite, live checks and remaining limits.

## Baseline verification

The existing suite passed **24/24 tests** in 1.63 seconds; `py_compile` passed for the five project Python files. This is only the existing suite's result, not evidence that the product works universally.

I independently reproduced these behaviors against the inspected source:

| ID | Severity | Reproduction and actual behavior | Expected behavior | Planned fix |
|---|---|---|---|---|
| QA-01 | High | Mock `/enheter/923609016` with a response whose `organisasjonsnummer` is `999999999`; the profile is returned as `verified` and publishes the other company's legal name. | Do not publish company-specific facts unless the response identifies the requested organization number. | Validate response type and exact organization number before snapshotting or publishing facts; emit a failed identity result on mismatch or malformed identity response. |
| QA-02 | High | `load_orgs()` given JSON string `"923609016"` or `{"companies":"923609016"}` returns nine single-character inputs. JSON `null` and a numeric root raise `TypeError`; an unknown object silently returns zero rows. | Accept only documented batch shapes, preserve valid duplicates, and reject malformed or unsupported shapes with an actionable error before output files are opened. | Validate root/container/record types and make JSON, JSONL and plain-text parsing explicit. Keep row order and duplicates. |
| QA-03 | High | Write a valid JSONL row containing U+2028 inside a string; `make_dashboard()` splits inside the JSON string and raises `JSONDecodeError`. The report loop uses the same unsafe `splitlines()` pattern. | JSONL records must split only at physical LF record boundaries; Unicode separators inside JSON strings must survive. | Read JSONL by `\n` delimiters (handling optional CR) in both dashboard and report paths. |
| QA-04 | Medium | Call the website collector with registered URL `https://example.no:bad`; reading `parsed.port` raises before the collector's error handling, allowing the company-level wrapper to discard otherwise valid registry facts. | A malformed registered site should produce a website failure/ambiguous status while preserving verified registry facts and terminal statuses. | Move URL parsing and validation inside the collector's guarded path and terminate all website-related claim families explicitly. |
| QA-05 | High | Parser evidence includes text from `<script>` and `<style>` blocks. The website entity check uses substring matching, so organization number `923609016` matches text `1923609016`. | Ignore non-visible script/style content and match an organization number as a distinct nine-digit token. | Track and omit non-visible element content; use numeric boundaries for organization-number matching. |
| QA-06 | Medium | Financial code assigns fields ending `_nok` without requiring NOK currency and accepts non-finite floats if present in a parsed payload. | Never label EUR or NaN as a NOK financial amount; malformed records must not corrupt valid statements. | Require supported currency for `_nok` fields, reject non-finite values, and isolate bad statements so valid matching records can still be evaluated. |
| QA-07 | Medium | Entity facts attach `registreringsdatoEnhetsregisteret` as `valid_at` to unrelated facts such as employee count, website and address. | Distinguish observation time from a field's actual validity date; do not claim an unrelated date is the fact's valid date. | Keep `observed_at`; set `valid_at` only where the source documents a field-specific effective date. |
| QA-08 | Medium | Optional search results are candidates only, but the batch runner increments outbound request counts for every verified profile even after the search judge's one-call budget is exhausted. The project also records API spend as `0.0` although provider billing is unknown. | Count actual calls and report unknown provider cost as unknown, not zero. | Return `calls_made` from the helper and aggregate it; keep provider-billed cost `null`/unknown when a live call occurs. |
| QA-09 | Medium | If `--output` resolves to the same file as `--input`, the CLI replaces the user's input with result JSONL after loading it. | Refuse a destructive input/output path collision before running. | Compare resolved paths and report a clear CLI error before creating state or output files. |
| QA-10 | Low, security review only | `public_host()` resolves and checks an address, then `urllib` resolves the hostname again when connecting. DNS rebinding could make the checked address differ from the connected address. No exploit was attempted. | The connection should be bound to the address that passed the public-IP check, or the risk should be explicitly accepted and constrained. | Investigate a safe connection strategy compatible with HTTPS certificate checks. If not safely fixable in this change, document as a residual SSRF/DNS-rebinding risk rather than claiming the check fully prevents it. |
| QA-11 | Medium | The optional judge accepts a candidate with a conflicting organization number if its self-reported name matches, and accepts a 2010 `as_of` date without a staleness warning. | A supplied organization number must match exactly; stale or invalid dates must be rejected or clearly marked stale. | Treat an explicit mismatched number as disqualifying even when the name matches; validate `as_of` and reject dates older than one year as stale candidates. |
| QA-12 | Medium | A valid identity response with `forretningsadresse` set to a string raises `AttributeError` while extracting nested fields, aborting the rest of the company profile after the identity lookup. | Keep the verified identity and other valid fields; ignore malformed optional nested objects and mark unavailable fields honestly. | Type-check registry nested objects (`organisasjonsform`, `naeringskode1`, `forretningsadresse`) before reading subfields; add a regression fixture. |

Other scope boundaries: active jobs are a narrow same-host heuristic; `public_activity` is explicitly unimplemented; the optional web-search helper returns unverified candidates and does not fetch/compare cited pages. The five external reference projects are not dependencies and have not been installed or benchmarked. These are limitations, not defects that can be fixed by merely relabeling their architecture reviews.

## Planned test and fix sequence

1. Add focused regression tests for the reproduced cases and adversarial payloads, keeping each feature's fixture tests separate from live tests.
2. Add source-code fixes only for QA-01 through QA-09 where a local fix is safe and supported by tests. Keep the DNS-rebinding item under security review unless the implementation can pin and verify the connected address without weakening TLS validation.
3. Run the complete suite and compile checks after changes; perform a clean package extraction and rerun the suite there.
4. Attempt no more than two live BRREG identity lookups from the existing seeded public sample, stopping after the first network/DNS failure. Check OpenAI-key presence without printing it; do not make paid calls unless a usable key is present.
5. Update this report with measured outcomes, exact commands, security findings, API-call/cost status and prioritized remaining work. Never promote fixture results to live-source results.

## Final QA results

### Fixes and verification

The source changes fixed QA-01 through QA-09 and QA-11 with focused regression coverage. QA-12 was found during the final edge-case pass, recorded above before the source edit, then fixed by type-checking optional nested registry objects. Malformed optional address, industry or organization-form objects are now omitted while a number-matched registry identity and unrelated valid fields remain available. The profile still reports those individual fields as absent rather than inventing values.

QA-10 remains a documented security limitation. The code rejects private/non-global DNS answers and unsafe redirects, but `urllib` resolves a hostname again when establishing the connection. The tested code does not pin the TCP connection to the address checked earlier, so DNS rebinding is not fully prevented. No rebinding or private-network request was attempted.

Final local verification on Python 3.13.15 / Windows 11:

```text
python -m unittest discover -s . -p 'test_*.py' -v
Ran 48 tests in 2.168s — OK

python -m py_compile signalpost_agent.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py
Exit code 0
```

The suite includes fixture-based identity, roles, finance, website, jobs, HTML escaping, snapshots/change behavior, malformed batch shapes, candidate judge logic and a 100-input complete-terminal-result batch case. The suite's network calls are mocked. Passing those tests establishes behavior against the stated fixtures, not real-source availability, data completeness, or competition score. The earlier pre-fix baseline suite passed 24 tests; the independent adversarial regression set exposed 11 failing assertions and 8 errors across 14 regression methods before fixes. The expanded final suite passes 48/48.

### Live checks and provider cost

One bounded live identity lookup was attempted on 18 October 2026 for `919951486`, the first seeded sample number in `smoke_100.json`, against `https://data.brreg.no/enhetsregisteret/api/enheter/919951486`, with a four-second timeout. It failed in 0.137 seconds with `URLError: [Errno 11001] getaddrinfo failed`. Following the QA instruction, the live run stopped after that first DNS failure. Therefore this QA turn verified **zero live company identities, finance records, websites or roles**. Do not interpret the fixture-backed tests as live validation.

`OPENAI_API_KEY` was absent and `SIGNALPOST_JUDGE_MODEL` was unset. No model/search-provider request was made; measured external API spend for this QA turn was **USD 0.00**. This is a measured QA cost, not a forecast for future use. If enabled later, the judge's provider cost remains unknown (`null`) unless billing data is available.

### Security and failure handling

Fixture probes confirm invalid checksums stop before a source call; registry organization-number mismatch fails closed before evidence is created; malformed JSON roots and JSONL records fail with an actionable input error; invalid URLs, userinfo, non-default ports and malformed ports do not reach the website opener; a robots disallow prevents the homepage request; cross-domain redirects are rejected; website organization-number matching requires a distinct digit token; script/style text is excluded from visible text; dashboard output escapes untrusted content; non-NOK and non-finite financial values are excluded; and stale or explicitly cross-entity judge candidates are rejected. HTTP 404/410, 401/403/429 and other errors map to distinct terminal statuses. These are mocked/unit probes, not penetration-test certification.

The program safely records missing or blocked sources with explicit statuses; they do not remain silently blank and are never replaced with zero. The QA also found that jobs coverage is a narrow same-host heuristic, `public_activity` is not implemented, and optional search output remains an unverified candidate until its source page is fetched and checked. Those are capability/coverage gaps, not test failures.

### Reference-project scope and architecture comparison

This QA did not install or execute Fire Enrich, `company-research-agent`, Dataforge, `python-brreg` or Crawl4AI as separate frameworks. The local Signalpost code is a standard-library implementation. The comparison material in the engineering report is architectural desk research, not a reproducible head-to-head benchmark. The practical tests covered Signalpost's own adapters and representative behaviors using mocks; they do not support claims that the five references were individually run, integrated, or comparatively scored. This choice avoided adding heavyweight dependencies and time/cost exposure while source networking was unavailable, but it leaves the requested comparative performance experiment open.

The implemented flow is: strict input normalization and Norwegian checksum validation → exact BRREG identity anchor → independently statused roles/accounts/website claims → source snapshots with hashes and retrieval dates → field facts with evidence IDs → deterministic summary and JSONL/HTML/report outputs. Website and accounts work are bounded and optional; the registry is the identity authority. The design favors entity precision, provenance, graceful abstention and a reproducible one-command batch run over broad web-research coverage.

### Readiness and next work

The local code is test-clean against 48 automated fixtures and ready for another review. It is **not validated as a successful or qualifying competition submission**: live DNS prevented source checks, official scoring data is unavailable, no judge API was configured, and breadth gaps remain. The existing 100-company sample output from an earlier run is historical evidence described in other project reports; it was not regenerated in this QA turn and should not be presented as a fresh result.

Before claiming submission readiness, rerun a small live sample from a network that can resolve and reach BRREG, verify the published registry/account schemas against current official responses, inspect the resulting evidence manually, then run the full 100-company sample and measure request count, runtime, useful-field coverage and cost. Separately, evaluate each reference project in an isolated environment only if time and dependencies permit; record exact versions and commands. Resolve the DNS-rebinding risk before treating website fetching as robust against hostile DNS, and implement/measure broader permitted job and dated public-activity sources if the competition rubric rewards them.

## Review of user-supplied QA artifacts (18 October 2026)

The attached report and patch describe a clean clone at commit `b4767fc9ab3a4f0f3ebddf9f656456d9003c3fe1`. They are useful independent QA work, but they are not a drop-in replacement for this workspace: the attached ZIP does not contain this workspace's optional `claim_judge.py`, and would remove that component if extracted over the current project. The patch's core findings overlap substantially with the earlier fixes in this project (identity-number checks, malformed batch handling, JSONL separators, financial validation, website parsing, URL/output collision protection, and explicit failure claims).

I ran the attached ZIP's 96-test suite on Windows 11 / Python 3.13.15 with its scratch directory redirected into the workspace. It did not reproduce the report's `95 pass, 0 fail, 1 expected-failure` result here: 2 loopback HTTP tests errored because this sandbox denies local socket connections (`WinError 10013`), 1 dashboard JavaScript round-trip assertion failed on the U+2028/U+2029 case, and 1 test was the documented expected failure. These results are environment-specific; they do not establish that the report's Linux result is false, but the reported total was not independently reproduced here.

I selectively adopted three changes into the current project: BRREG's documented entity-status facts (`konkurs`, `konkursdato`, `underAvvikling`, `underAvviklingDato`, and `underTvangsavviklingEllerTvangsopplosning`) are now included with source evidence; ISO date values are validated before becoming status dates; and the final envelope builder repairs missing, duplicate or invalid claim families instead of relying on assertions that can disappear under optimized Python. Redirect URLs containing username/password are now rejected. Tests cover these changes.

I did not add `slettedato` as a main-entity fact because the attached report itself labels that field unverified for `/enheter`; BRREG documents it in the under-entity section. I also did not replace the current project with the supplied ZIP. The official BRREG documentation confirms the status fields and shows `person.navn` as an object; it also records that `fratraadt` was removed from the public API in June 2026. The current parser tolerates the nested name shape and ignores `fratraadt` only when present in a legacy response, while using `avregistrert` for the current schema. [BRREG Enhetsregisteret API documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html)

After those selective changes, the current project suite passes **51/51 tests** and Python compilation passes. Live-source checks remain blocked by DNS as noted above. No changes were pushed to GitHub.

## Review of the additional D: drive QA files (18 October 2026)

Reviewed `D:\signalpost_agent.py`, `D:\test_qa_regressions.py` and `D:\TEST_REPORT.md` against the current project. The new report's claim that BRREG has only flat `person.fornavn` / `person.etternavn` fields is not supported by the current official `/roller` example: it documents `person.navn.fornavn`, `person.navn.mellomnavn` and `person.navn.etternavn`. The supplied test uses a flat name layout. The current parser already handled the documented nested layout, so replacing it with the flat-only implementation would regress documented responses. [Official BRREG API documentation](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/no/index.html)

I kept the existing official nested parsing and added compatibility for flat name fields, plus guards that skip malformed role groups and entries while preserving valid people in the same response. I did not copy the other changes wholesale: the current project already rejects non-ASCII numerals, validates input roots, handles malformed registry objects, and guards financial extraction. The candidate is a distinct project revision and does not include the current optional judge; it should not replace this project. Its report also records that live checks could not reach BRREG because DNS was blocked.

The current regression suite now covers official nested names, flat compatibility names and malformed role entries. Verification on Windows 11 / Python 3.13.15:

```text
python -m unittest discover -s . -p 'test_*.py' -v
Ran 54 tests in 2.251s — OK

python -m py_compile signalpost_agent.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py
Exit code 0
```

These are fixture-backed checks and do not establish a competition score.

## Follow-up completion: live batch, finance correction and remaining safeguards (18 October 2026)

Enabled live network access and reran the 100-company public sample after discovering that the accounts adapter used `?orgNummer=...` even though the official Regnskapsregister OpenAPI defines `GET /regnskapsregisteret/regnskap/{orgNummer}`. The corrected route is `https://data.brreg.no/regnskapsregisteret/regnskap/{org}`. This is a substantive API bug fix: the first live rerun produced 100 finance 404s; the corrected run returned finance facts for 99 companies and one explicit HTTP 500. [Official BRREG Regnskapsregister API specification](https://data.brreg.no/regnskapsregisteret/regnskap/v3/api-docs)

Live run command:

```text
python signalpost_agent.py --input smoke_100.json --output outputs/live-100-final.jsonl --state outputs/live-100-final.sqlite3 --timeout 5 --workers 4
```

Measured result: 100 inputs, 100 output envelopes, 100/100 identity lookups verified, 100/100 roles available, 99/100 locations available, 99/100 financials available and one finance HTTP 500; web presence was available for 1, ambiguous for 4, not available for 92, and failed for 3. Jobs were available for 1, not available for 96 and failed for 3. Public activity was not available for 97 and failed for 3. Total: 314 outbound requests, 95,067 ms wall time, 378,156 ms summed per-company runtime, four reported errors and USD 0.00 paid-provider cost. The failed source cases remained explicit; the collector published no invented values. The machine-readable files and dashboard were refreshed in `outputs/live-100-final.jsonl`, its report and HTML companion. These are actual live responses from this run, not fixtures. One live 500 and three company-site access failures remain source-side limitations.

Three targeted changes address the remaining implementation gaps. Website parsing now checks same-host news/press/blog links, obeys the checked robots policy, requires the exact legal name or organization number on the activity page, requires a page publication date, and stores a dated evidence snapshot. Optional web-search candidates now stay candidates until their cited pages are fetched; a candidate is accepted only when the cited page is robots-allowed and same-host after redirects, its visible text contains the quoted passage and exact company identity, and its publication date matches the candidate date. The fetch is limited to three citation pages per search response and its requests are counted. Finally, the HTTPS opener now resolves host addresses once per connection, rejects any non-public answer, connects to the checked IP address, and still verifies TLS against the original hostname; proxy tunnels are disabled so a proxy cannot become the actual peer. The 1,000-company behavior is covered by a mocked integration test; the official submission requires a 100-company smoke test and says precomputed company profiles are not required.

Final local verification after these changes:

```text
python -m unittest discover -s . -p 'test_*.py' -q
Ran 59 tests — OK

python -m py_compile signalpost_agent.py claim_judge.py test_signalpost_agent.py test_input_stages.py test_claim_judge.py test_qa_regressions.py
Exit code 0
```

The 59-test suite includes a full 1,000-company fixture batch and regressions for nested/flat role names, dated activity extraction, cited-page quote/entity/date checks and private-IP rejection before opening a socket. The 100-company live result is available, but Builderr's score can only be known from its evaluator. The five reference frameworks were not individually installed and benchmarked in this completion pass; their comparison remains architectural analysis, not head-to-head measured evidence.

## Follow-up hardening: transient retries and provenance gate (18 October 2026)

The single finance HTTP 500 was retried once against the same production endpoint for organisation `987441623` (`STIFTELSEN VELFERDSSAMBANDET TELENOR BERGEN`). Both attempts returned HTTP 500. That is repeatable evidence of a source-side failure for this profile; it does not establish whether the account is unavailable or BRREG has a record-specific fault. The claim remains `failed`, with no substitute amount. The GET wrapper now makes one 250 ms retry for connection/timeout errors and HTTP 500/502/503/504 only. It does not retry 404/410, 401/403/429, parse errors or other non-transient responses, and it increments outbound request counts for each retry.

The final profile builder now independently validates every fact's provenance before output: the identity must be verified; all referenced evidence and source snapshots must exist; the snapshot must be for the requested organisation and permitted source family; the evidence must describe the fact field; optional fact source URLs must match the snapshot URL; and values must be JSON-safe and non-empty. Facts that fail are removed and logged as `evidence_integrity`; an otherwise empty `available` claim is changed to `failed`. This layer checks internal evidence linkage and identity consistency. It does not independently prove the truth of arbitrary website prose, which is why website extractors still require exact entity matching and LLM candidates require fetching and checking citations.

New fixture regressions cover retry-after-500, no retry on 404, wrong-organisation snapshots, mismatched evidence fields and valid evidence preservation. The full suite now passes **63/63**. A fresh live one-company registry/roles check passed through the new gate with verified identity, 11 registry facts and 14 role facts; finance was `not_available` for that company, with no errors. The formerly 500 finance case remained failed after two requests to the account endpoint. The full 100-company run has not been repeated since this code-only hardening; its earlier measured report remains the previous version's live evidence.
