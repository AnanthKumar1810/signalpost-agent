# Submission manifest

Prepared: 18 October 2026

- Repository: https://github.com/AnanthKumar1810/signalpost-agent
- Historical live smoke-run source revision: `82d9ceb748ffeff3c5f8b22dd740fa017f204f2e`. The recorded live measurements below belong to that run. Subsequent source changes are tested offline; they have not been included in a new live 100-company run.
- One-command run from a clean checkout (Python 3.10+; standard library only):
  `python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 12 --workers 4`
- Reproduce the recorded 100-company smoke run:
  `python signalpost_agent.py --input smoke_100.json --output outputs/live-100-coverage-final.jsonl --state outputs/live-100-coverage-final.sqlite3 --timeout 5 --workers 4`
- Recorded run: 100 input companies; 100 output envelopes; 100 verified identities; 322 outbound requests; 108,675 ms wall time with four workers; 4 reported errors. Finance returned data for 99 companies and one lookup ended in HTTP 500 after retry. Jobs and public activity each had 1 available, 96 not available, and 3 failed. Paid-provider spend was USD 0.00. See `outputs/live-100-coverage-final.jsonl.report.json` and the detailed judge report.
- Model/API details: default mode uses no LLM and no paid search API. It uses BRREG registry, roles and accounts APIs, and conditionally checks the company's BRREG-listed website and its advertised RSS/Atom feeds. Optional NAV job search is available with `--nav-jobs`; the measured capped run found no matches. The optional LLM-assisted candidate search requires an API key; cited pages are fetched and checked before claims can be marked verified. It is not part of the default run.
- Optional website discovery: `--wikidata-websites` queries Wikidata for an exact Norwegian organisation-number match and treats its website property as a candidate only. The candidate site still has to pass the agent's independent website identity and fetch checks. This path is not live-verified here and must not be included in measured coverage or the measured run command above.
- Expected external API cost: measured USD 0 for the recorded default run. This excludes local compute and internet service charges. Any optional provider use incurs provider-specific costs that were not measured here.
- Contact: Sirela Ananth Kumar, `sananthkumar3@gmail.com` (from the participant details provided for this project).
- Test inputs and outputs: `smoke_100.json`, `smoke_100_sample.json`, and `outputs/live-100-coverage-final.jsonl` with its JSON report and HTML viewer.
- Source and reuse: BRREG open data is published under NLOD 2.0. The accounts service is described as a preview and may be incomplete or unavailable. Third-party websites remain subject to their access rules and robots policies. Restricted social platforms are not scraped.
- Official submission status: prepared for the participant to submit. No competition email or official submission has been sent. The official score and qualification can only be determined by Builderr's evaluation.

## Profile-count requirement

Builderr's current detailed “What to submit” instructions request a 100-company smoke-test report with one complete result per input. They also say Builderr provides the companies for official evaluations and precomputed company profiles or a company manifest are not needed for ranking. The public page also contains a shorter “Submit 1,000+ profiles” line, so the page is internally inconsistent. This bundle includes the explicitly detailed 100-company smoke test and the reusable agent; it does not claim that the 100 smoke profiles are a 1,000-profile dataset. Confirm the requirement with Builderr before relying on the shorter banner line.

Official rules: https://builderr.ai/challenges/signalpost
