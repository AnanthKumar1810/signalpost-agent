# Signalpost judge report

**Date:** 17 October 2026  
**Judgment:** A numerical score cannot be measured locally because Builderr's checked comparison collection is hidden. The earlier 44/100 estimate below was made before the accounts API route was corrected and is superseded; do not use it as a score for the current build.

## Current assessment: no verified numerical score

The current live batch verified 100/100 identities and roles, returned financial data for 99/100 companies, and completed 100/100 envelopes. It found a verified company website for 1 company and an active vacancy for 1 company. This supports reliable execution and identity precision, but it does not reveal recall against Builderr's hidden verified corpus. No official score or qualification can be inferred from these counts.

The current smoke run used a deterministic random sample of 100 companies from the public universe. It returned 100 complete envelopes in 95.1 seconds wall time using four workers, with 314 requests and USD 0.00 paid API costs. The corrected accounts route succeeded for 99 companies; one request received HTTP 500. The machine-readable details are in `outputs/live-100-final.jsonl.report.json`.

Identity, roles and evidence provenance are the strongest areas. Coverage is still limited: the website/job collector checks the BRREG-listed company site and a small number of same-host pages, while public activity is extracted only from dated, entity-matched company-site articles. More permitted sources would likely improve recall. An updated numerical estimate is not provided because local measurements cannot be mapped reliably to Builderr's hidden checked collection.

Builderr's score should be treated as authoritative. Current requirements and the 100-company smoke-test rule are on [Builderr's Signalpost page](https://builderr.ai/challenges/signalpost).

The project is packaged for review, but no repository commit hash is claimed here and no competition email or submission was sent.
