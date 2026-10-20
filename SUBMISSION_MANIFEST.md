# Signalpost submission-review manifest

Prepared 20 October 2026. **No competition email or official submission has been sent.** Review the [current Builderr instructions](https://builderr.ai/challenges/signalpost) before submission. The official qualifying score is unknown.

- Repository: https://github.com/AnanthKumar1810/signalpost-agent
- Exact revision: use `git rev-parse HEAD` in the final pushed checkout. The revision is intentionally not hard-coded inside its own commit.
- Contact supplied earlier by participant: Sirela Ananth Kumar, `sananthkumar3@gmail.com`.
- Runtime: Python 3.10+; standard library only. `pip install -r requirements.txt` installs no third-party packages.
- One-command run: `python signalpost_agent.py --input smoke_100.json --output outputs/profiles.jsonl --state outputs/state.sqlite3 --timeout 10 --workers 8 --nav-jobs --wikidata-websites`
- Larger local sample: replace the input with `submission_1000.json` and choose new output/state paths. The fresh 1,000-company result is `outputs/submission-1000-final.jsonl` with adjacent `.report.json`; it returned 1,000/1,000 verified identities in 662.498 seconds and 3,313 requests. The 100-company output and report are in `outputs/final-100-smoke.jsonl` and its adjacent `.report.json`.
- APIs: BRREG official registry, roles, and accounts; robots-permitted first-party sites/feeds; public ATS boards linked from verified sites; NAV open vacancy feed; exact-number Wikidata website discovery. Search results are candidates only, never published facts.
- Model: none in the recommended run. Optional OpenAI summary is key-gated and fixture-tested, not live-tested. Optional Brave and TinyFish discovery are disabled; TinyFish requires a private participant key and is not suitable as an evaluator dependency without a Builderr-provided key.
- Measured paid-provider cost for the no-key 100- and 1,000-company runs: USD 0; local compute, network, and hosting are not measured. Optional provider costs are not included.
- Source reuse: BRREG open data is NLOD 2.0; Wikidata structured statements are CC0. The accounts API is a preview and can fail. Company websites obey robots/access checks. Restricted social platforms are not scraped.
- Validation: run `python -m unittest discover -s . -p 'test_*.py' -q` and `python audit_profiles.py outputs/submission-1000-final.jsonl`. A fresh committed-source archive passed 106 tests and a one-company live CLI run with one verified identity and zero lineage violations. This environment could not bootstrap a `venv` or execute a direct local Git clone, so the archive and system Python were used. `FINAL_TECHNICAL_REPORT.md` explains the baseline comparison, failures, decisions and limits.
- Dashboard: `docs/index.html` is a frozen practice sample. The local CLI also creates an HTML browser beside each JSONL output. GitHub Pages is not claimed live until the repo owner enables and verifies it.
- Risk: measured website/jobs/activity coverage remains low. This package is runnable and reviewable but **not proven to reach 65/100**.
