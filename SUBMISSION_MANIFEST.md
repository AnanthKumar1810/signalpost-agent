# Submission manifest

Prepared: 19 October 2026

- Repository: https://github.com/AnanthKumar1810/signalpost-agent
- Tested code revision (pushed to `main`): `c20e346b5a2b0a258a150a0cb02f88421cb1cfcb`. The original baseline and 19 October live measurements are identified separately in the judge report; never attribute old measurements to a newer revision.
- One-command run from a clean checkout (Python 3.10+; standard library only):
  `python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 12 --workers 4 --nav-jobs --wikidata-websites`
- Reproduce the recorded final combined 100-company smoke run:
  `python signalpost_agent.py --input smoke_100.json --output outputs/live-100-combined-final.jsonl --state outputs/live-100-combined-final.sqlite3 --timeout 8 --workers 4 --nav-jobs --wikidata-websites`
- Final combined run: 100 input and output profiles, 100 verified identities, 348 outbound requests (28 NAV requests and 2 Wikidata queries), 177,328 ms wall time using four workers, 4 source errors, and USD 0 paid API cost. Website presence was 1 available / 92 not available / 4 ambiguous / 3 failed. Jobs and public activity each were 1 available / 96 not available / 3 failed. NAV found two employer candidates and matched zero companies; Wikidata checked 92 website-less profiles and returned zero candidates. A structural audit of 2,923 facts found no missing evidence IDs, missing snapshots, wrong-company snapshots or evidence-integrity errors. These figures do not establish fact truth or score coverage. The detailed judge report and `outputs/live-100-combined-final.jsonl.report.json` provide run details.
- Model/API details: no LLM or paid search API is used in the recommended run. The agent uses BRREG registry, roles and accounts APIs; it checks a company's registered/verified website, advertised RSS/Atom feeds and same-site pages; it checks public Greenhouse, Lever or Ashby jobs only when that website explicitly links to the board. NAV is bounded to a 180-day update window, 25 feed pages and 100 details by default. Wikidata's exact organisation-number match provides only an untrusted website candidate; every candidate site must pass the independent identity and fetch checks. Optional LLM-assisted search remains candidate-only until cited pages are fetched and claims verified; it is not in the recommended command.
- Expected external API cost: the measured combined public-source run spent USD 0 on paid APIs. This excludes local compute and internet service charges. Optional LLM/search-provider use has separate cost and is not included.
- Contact: Sirela Ananth Kumar, `sananthkumar3@gmail.com` (from the participant details provided for this project).
- Test inputs and outputs: `smoke_100.json`, `smoke_100_sample.json`, and `outputs/live-100-combined-final.jsonl` with its JSON report and HTML profile browser.
- Source and reuse: BRREG open data is published under NLOD 2.0. The accounts service is described as a preview and may be incomplete or unavailable. Third-party websites remain subject to their access rules and robots policies. Restricted social platforms are not scraped.
- Official submission status: prepared for the participant to submit. No competition email or official submission has been sent. The official score and qualification can only be determined by Builderr's evaluation.

## Profile-count requirement

Builderr's current detailed “What to submit” instructions request a 100-company smoke-test report with one complete result per input. The page advertises running tests at 1,000+ profiles, and says Builderr supplies the companies for official evaluations; precomputed profiles are not needed for ranking. The bundle includes the reusable agent and the required 100-company smoke test; it does not claim that this smoke sample is a 1,000-profile data set.

Official rules: https://builderr.ai/challenges/signalpost
