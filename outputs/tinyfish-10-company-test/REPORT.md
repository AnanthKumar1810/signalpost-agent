# TinyFish-assisted 10-company website-discovery evaluation

Run date: 2026-10-19 UTC

## Scope and sample

This evaluation uses the first 10 organisation numbers from the checked-in reservoir sample. The parent sample was selected from Builderr's public eligible-company universe with seed `20261017`. Search results are candidate leads only. No search title, snippet, directory page, or aggregator page was published as company evidence.

| Organisation number | BRREG legal name |
|---|---|
| 919951486 | ARV INVEST AS |
| 991353240 | TU SAMDRIFT DA |
| 931671081 | VVS COMPANIET AS |
| 822021972 | PEPPERBUSKEN AS |
| 980383318 | RETOMA AS |
| 992120193 | ARENA TRONDHEIM AS |
| 922351945 | SOLBERG EIENDOM KVINESDAL AS |
| 912161471 | KONGSVEIEN 15 AS |
| 987441623 | STIFTELSEN VELFERDSSAMBANDET TELENOR BERGEN |
| 984735634 | THORESEN HOLDING AS |

The exact input is `outputs/tinyfish-10-company-test/input.json`.

## Commands

Baseline:

```powershell
python signalpost_agent.py --input outputs\tinyfish-10-company-test\input.json --output outputs\tinyfish-10-company-test\baseline-live.jsonl --state outputs\tinyfish-10-company-test\baseline-live.sqlite3 --timeout 12 --workers 4 --wikidata-websites
```

TinyFish candidate discovery used one exact query per company:

```powershell
tinyfish search query "<organisation number> <BRREG legal name>" --location Norway --language no
```

Candidate pages were fetched with `tinyfish fetch content get` only for the two BRREG-listed domains and the final Arena Trondheim destination. Search result snippets were never used as evidence.

Improved rerun:

```powershell
python signalpost_agent.py --input outputs\tinyfish-10-company-test\input.json --output outputs\tinyfish-10-company-test\improved-live-retry.jsonl --state outputs\tinyfish-10-company-test\improved-live-retry.sqlite3 --timeout 12 --workers 4 --wikidata-websites
```

Verification:

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
python -m py_compile signalpost_agent.py test_qa_regressions.py
```

## Measured results

| Measurement | Baseline | Improved retry |
|---|---:|---:|
| Complete terminal profiles | 10/10 | 10/10 |
| Verified BRREG identities | 10/10 | 10/10 |
| Verified websites | 0/10 | 1/10 |
| On-site jobs results | 0/10 | 1/10 |
| Public-activity results | 0/10 | 0/10 |
| Financial results | 9/10 | 9/10 |
| Agent outbound requests | 35 | 37 |
| Wall runtime | 20.056 s | 12.898 s |
| Recorded errors | 2 | 1 |
| Direct provider cost reported by agent | $0.00 | $0.00 |

Runtime varies with source latency, so the faster improved run is not attributed to the code change. The improvement is the one additional verified website and one on-site jobs result.

TinyFish usage for this evaluation was 10 successful search calls. Search usage history confirms one query for every organisation number. The searches returned 97 ranked results in total. The measured search-command runtime was approximately 49.3 seconds. Four TinyFish content-fetch commands were attempted: one optional highlights request was rejected with HTTP 403 because the account does not enable highlights; the ordinary fetches attempted five URLs and took approximately 22.4 seconds in total. TinyFish did not expose billing data in these command results, so its exact cost is not measured and is not reported as zero.

## Accepted website

`992120193 — ARENA TRONDHEIM AS`

- BRREG lists `www.arenax.no` as the registered website.
- The legacy domain redirects through `http://arenatrondheim.no/` to `https://arenatrondheim.no/`.
- The improved fetcher never requests the plaintext hop. It upgrades the redirect target to HTTPS, checks that the destination resolves only to public addresses, and fetches the final site with TLS hostname verification.
- The final site identifies the distinctive company name `Arena Trondheim`; its jobs page explicitly says `Arena Trondheim AS`.
- Published website evidence: `https://arenatrondheim.no/`, snapshot `website:a1563cc39a98f762` in the measured rerun.
- The site advertises `https://arenatrondheim.no/ledige-stillinger/`. The page contains an Event Coordinator vacancy, an application email, and deadline text `Snarest`; the agent retained snapshot `jobs:2e034b9303f3256d`.
- The vacancy page has no publication date. It is currently advertised on the company site, but its age cannot be confirmed. This freshness limitation must remain visible to reviewers.

## Rejected and unresolved candidates

| Company | TinyFish result assessment | Final status |
|---|---|---|
| ARV INVEST AS | Results were Kredittsjekk, Proff, BRREG and unrelated foreign pages. No company-owned site was found. | Not available |
| TU SAMDRIFT DA | Results were Proff, 1881, Firmadatabasen and BRREG. All are registries/directories. | Not available |
| VVS COMPANIET AS | Results were BRREG, Utdanning.no, Proff, Rørleggerplassen and Firmadatabasen. No official site was established. | Not available |
| PEPPERBUSKEN AS | Results were BRREG, Proff, Utdanning.no, Forretningsflyt and Unrealfund. No official site was established. | Not available |
| RETOMA AS | Results were Tracxn, Proff, Vexter, BRREG and IO.no. BRREG lists `www.retoma.com`, but TinyFish returned `empty_content` and the agent could not independently match the exact entity. | Ambiguous |
| ARENA TRONDHEIM AS | Search results were aggregators/BRREG, but the BRREG-listed legacy domain led to the verified replacement domain described above. | Available |
| SOLBERG EIENDOM KVINESDAL AS | Results were Proff, Purehelp, Byndle, Eiendomssjekk and Firmadatabasen. No company-owned site was found. | Not available |
| KONGSVEIEN 15 AS | Results were Proff, Forretningsflyt, Purehelp, BRREG and Kredittsjekk. No company-owned site was found. | Not available |
| STIFTELSEN VELFERDSSAMBANDET TELENOR BERGEN | Results were Kredittsjekk, 1850, BRREG, Foretaksinfo and Yelp. No company-owned site was found. | Not available; BRREG finance HTTP 500 |
| THORESEN HOLDING AS | Results were Proff, BRREG, Finansavisen, Gule Sider and Firmadatabasen. No company-owned site was found. | Not available |

Directory and aggregator pages can corroborate that a legal entity exists, but they are not treated as the company's official website and cannot unlock company-owned jobs or activity collection.

## Change implemented

The previous redirect handler rejected every cross-host redirect. This caused the BRREG-listed `www.arenax.no` legacy domain to fail before the final company's identity could be checked.

The new behavior is deliberately narrow:

1. Cross-host redirects remain prohibited for Wikidata, Brave/TinyFish, and other untrusted candidates.
2. Only a website URL supplied directly by BRREG may redirect to another host.
3. Redirect targets must have no credentials, use the standard HTTPS port, and resolve only to public IP addresses.
4. An HTTP redirect location is upgraded to HTTPS before a network request; no plaintext request is sent.
5. The final page must still pass the existing legal-name or exact-organisation-number identity gate.
6. Jobs and activity collection remain on the verified final host and continue to honor its robots policy.

The implementation changes are in `signalpost_agent.py`; the regression is covered by `test_registered_redirect_upgrades_to_https_and_rechecks_identity` in `test_qa_regressions.py`.

## Verification and failures

- Full automated suite: 95/95 tests passed.
- Python compilation: passed.
- `git diff --check`: passed; Git emitted only the repository's Windows line-ending warning.
- The first sandboxed baseline failed all BRREG lookups with DNS `getaddrinfo failed`; it is preserved as `baseline.jsonl` and is not used for coverage conclusions.
- The first improved live run found the Arena website but Wikidata timed out. A single controlled retry completed Wikidata successfully and is the comparison result above.
- BRREG's financial endpoint returned HTTP 500 for organisation `987441623` in both valid live runs. No financial value was guessed.
- TinyFish highlights mode returned HTTP 403 and triggered a Windows CLI assertion after the error. Ordinary content fetching succeeded and was used instead.

## Remaining limitations

- Verified website coverage is still only 1/10 in this sample. TinyFish found no defensible company-owned site for eight companies, and Retoma remained ambiguous.
- At the time of this diagnostic, TinyFish was external to the production batch. The optional integration and its stricter candidate filter were added afterward; see the 20 October follow-up below.
- The Arena jobs page lacks a publication date, so current availability is visible but freshness is not proven.
- Public activity remained 0/10.
- The result is a 10-company diagnostic sample, not a statistically reliable estimate for the full universe or the hidden Builderr set.
- TinyFish billing was not exposed, so external cost is unmeasured.

## 20 October follow-up

The statements above describe the original 19 October diagnostic revision. TinyFish is now implemented as an **optional** bounded CLI discovery module, but it is excluded from the recommended evaluator command because that run cannot depend on the participant's private key. The first optional integrated test briefly accepted five directory/aggregator URLs as website candidates; this was a false-positive bug. Directory, social and weakly matching domains were subsequently rejected, and the isolated 10-company rerun found 21 remaining candidate URLs, **zero** additional verified company-owned websites, and the one independently BRREG-verified Arena website. The current suite passes 106 tests. The generic Arena careers page is now labeled `careers_page_url`, not an active or dated vacancy. See `FINAL_TECHNICAL_REPORT.md` for the current whole-project state and publication status.
