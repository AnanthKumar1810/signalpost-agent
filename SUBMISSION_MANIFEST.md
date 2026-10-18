# Submission manifest (draft)

- Repository: https://github.com/AnanthKumar1810/signalpost-agent
- Exact commit: the final pushed `main` commit is reported in the delivery message and can be checked with `git rev-parse HEAD` after cloning the repository.
- One-command run:
  `python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 12 --workers 4`
- 100-company smoke command:
  `python signalpost_agent.py --input smoke_100.json --output outputs/live-100-final.jsonl --state outputs/live-100-final.sqlite3 --timeout 5 --workers 4`
- Runtime: the latest live integrated run returned 100 envelopes in 108,675 ms, using 322 requests and four workers. It recorded 1 available jobs profile, 1 available public-activity profile, and 99 available finance results; one finance request returned HTTP 500. The run report is beside `outputs/live-100-coverage-final.jsonl`. The 1,000-company automated test is fixture-backed and is not a measured live runtime.
- Model/API: default mode makes no LLM or paid search calls. Sources are BRREG entity/roles and accounts endpoints, plus a BRREG-listed company website when robots and URL safety checks pass. RSS/Atom is checked only when advertised by that registered site. The optional `--nav-jobs` mode uses NAV's public job feed; it was tested with a three-page cap and found no matches in the 100-company sample, so it is not enabled by the one-command default. The optional web-search judge requires `OPENAI_API_KEY`; its citations are now fetched and checked for exact quote, company identity and publication date before being marked verified.
- Cost: observed paid-provider API spend in the live 100-company default run was USD 0. Optional provider spend is unknown unless enabled and checked against provider billing. Local compute/network cost is not included.
- Source rights: BRREG open data is NLOD 2.0 as described in its published specifications. The accounts API is a preview service. The corrected production endpoint returned finance records for 99 of 100 companies in the recorded live sample; one entity returned HTTP 500 twice, including a targeted retry. The failure remains explicit. See `FETCH_FAILURE_ANALYSIS.md`. Each company website remains subject to its own terms and robots policy. The implementation does not scrape restricted social platforms.
- Test corpus: deterministic 100-row reservoir sample (seed 20261017) from Builderr's public universe, selected from all 411,160 rows. Sample metadata and the universe archive's published SHA-256 are in `smoke_100_sample.json`.
- Contact: not configured. The owner must supply a contact before a formal entry. No personal address has been inserted or sent.
- Official submission status: this GitHub push is code delivery only. It is not a Builderr email submission or official score/qualification.



