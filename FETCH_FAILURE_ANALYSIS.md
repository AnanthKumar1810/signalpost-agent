# Fetch failure diagnosis and resolution plan

**Date:** 17 October 2026  
**Scope:** The 100-company live batch in `outputs/smoke-100-final.jsonl` and a fresh endpoint probe.

## What failed

The 100-company batch returned all 100 profiles and verified all 100 legal identities. The main failure was isolated to annual accounts: all 100 financial claims were marked `failed`, and the error was `urlopen error timed out` for `data.ppe.brreg.no`. The batch made 313 requests; the accounts service alone generated 100 of the 103 recorded errors. The remaining three were website retrieval/robots-check errors on company-owned websites. They do not indicate a registry identity mismatch.

The project had configured the accounts request to BRREG's PPE (pre-production) host. BRREG's own developer documentation distinguishes PPE from PRD, lists the `data.brreg.no` host for production, and says the production deployment is exposed as a preview without a quality-of-service guarantee. The API catalog also notes limited coverage: it contains approved latest annual-account key figures for ordinary-format filers from accounting year 2018, while some industries and consolidated accounts are excluded. Thus two issues were conflated: our code selected the pre-production host, and the source itself offers limited coverage and reliability.

## Changes made

The agent now requests `https://data.brreg.no/regnskapsregisteret/regnskap`, the production URL published by BRREG. A regression test asserts that the PPE host is not selected. The response still must match the requested organisation number before any financial fact is published. A timeout or other transport error remains explicitly `failed`; an HTTP 404/410 remains `not_available`; no missing amount is converted to zero.

## Verification and current limitation

The full local suite passes **24/24 tests**, including exact-entity finance matching and the new production-host regression check. I probed the production and PPE URLs during this follow-up. Both failed immediately with `getaddrinfo failed`, and the ordinary Enhetsregisteret host failed the same way in the current execution environment. This indicates DNS/network unavailability for this session, so it is not valid to claim that the production finance request has now returned data. The production endpoint correction is verified against BRREG's published documentation and by a local regression test; live response verification is still pending a network with BRREG DNS access.

## Next steps, in order

1. On a network that resolves BRREG, run the one-company smoke test against a known company with a filed ordinary annual account. Confirm HTTP response shape, organisation number, reporting period, currency and at least one figure against the returned source record.
2. Test an entity expected to have no eligible account and a financial-sector entity. Ensure a true empty/unsupported response is distinguished from a transport outage.
3. Re-run the reproducible 100-company sample and compare `financials` statuses, error counts, request count and wall time with the archived run. Preserve both reports; do not overwrite the earlier evidence.
4. If production is intermittently unavailable, add a small bounded retry for transient connection/timeouts, then a per-run circuit breaker after repeated transport failures. Do not retry 404, 401/403, schema errors, or company-specific empty results. Ensure each skipped profile says the shared source was unavailable and that skipped requests do not count as outbound requests.
5. Keep website `robots.txt` and fetch errors per domain. Retry them only on transient network errors, obey crawl delay and the total request/time limits, and retain a clear failure status rather than treating blocked pages as empty.

## Retry and integrity follow-up (18 October 2026)

The agent now makes one short retry for a GET that encounters a timeout/connection error or HTTP 500, 502, 503 or 504. It counts every retry as another outbound request. It does not retry access denials, rate limits, missing records or malformed responses. A targeted live rerun of `987441623` reached BRREG's documented production accounts path twice; both responses were HTTP 500. The finance claim correctly remains failed. This may be a persistent BRREG endpoint/record issue; the agent has no safe basis to infer financial values or change the status to unavailable.

A second output integrity pass now requires a verified identity and matching evidence, source snapshot, organisation number, source family and fact field before publication. It drops inconsistent claims with an explicit `evidence_integrity` error. The full local suite passes 63 tests; a live one-company registry/roles profile passed the new validation gate. The full 100-company live batch should be rerun after this change when a short validation window is available; prior batch metrics are not attributed to the new gate.

## Source references

- BRREG developer documentation: [environments and API URL](https://github.com/brreg/regnskapsregister-api/blob/main/docs/for-devs.md).
- BRREG API catalog: [annual accounts API scope, limitations and license](https://github.com/brreg/openAPI/blob/master/specs/API-catalog.ttl).
