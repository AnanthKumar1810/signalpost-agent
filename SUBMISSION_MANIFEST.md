# Submission manifest (draft)

- Repository: https://github.com/AnanthKumar1810/signalpost-agent
- Exact commit: the final pushed `main` commit is reported in the delivery message and can be checked with `git rev-parse HEAD` after cloning the repository.
- One-command run:
- 100-company smoke command:
  `python signalpost_agent.py --input smoke_100.json --output outputs/live-100-final.jsonl --state outputs/live-100-final.sqlite3 --timeout 5 --workers 4`
- Runtime: the checked live run returned 100 envelopes in 95,067 ms, using 314 requests and four workers. The report is included beside the output. The 1,000-company automated test is fixture-backed and is not a measured live runtime.
- Model/API: default mode makes no LLM or paid search calls. Sources are BRREG entity/roles and accounts endpoints, plus a BRREG-listed company website when robots and URL safety checks pass. The optional web-search judge requires `OPENAI_API_KEY`; its citations are now fetched and checked for exact quote, company identity and publication date before being marked verified.
- Cost: observed paid-provider API spend in the live 100-company default run was USD 0. Optional provider spend is unknown unless enabled and checked against provider billing. Local compute/network cost is not included.
- Source rights: BRREG open data is NLOD 2.0 as described in its published specifications. The accounts API is a preview service. The historical batch used its PPE host and timed out; this package now targets production, but live response verification is pending. See `FETCH_FAILURE_ANALYSIS.md`. Each company website remains subject to its own terms and robots policy. The implementation does not scrape restricted social platforms.
- Test corpus: deterministic 100-row reservoir sample (seed 20261017) from Builderr's public universe, selected from all 411,160 rows. Sample metadata and the universe archive's published SHA-256 are in `smoke_100_sample.json`.
- Contact: not configured. The owner must supply a contact before a formal entry. No personal address has been inserted or sent.
- Official submission status: this GitHub push is code delivery only. It is not a Builderr email submission or official score/qualification.



