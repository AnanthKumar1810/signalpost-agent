# Signalpost evidence-first agent

This Python 3.10+ agent accepts Norwegian organisation numbers and emits one JSONL result per input. It uses the official BRREG entity and roles APIs as the identity anchor, and the public BRREG key-figures endpoint for financial evidence. A company website is considered only when BRREG lists it; the bounded HTTPS fetch checks `robots.txt`, rejects private-address destinations and cross-host redirects, and publishes claims only when the exact legal name or organisation number appears on the page.

## Run

```powershell
python signalpost_agent.py --input smoke.json --output outputs/smoke.jsonl --state outputs/state.sqlite3 --timeout 3
```

This command accepts a JSON array, JSONL objects with `organisation_number`, an object containing that field, or one organisation number per line. Replace `smoke.json` with Builderr's supplied batch for an official run. Each invocation overwrites the JSONL output and creates a machine-readable `.report.json` and local HTML profile browser beside it. The SQLite state file keeps content-addressed source history and detects material changes.

Python's standard library is the only dependency. Run offline contract and fixture tests with `python -m unittest -v`. The 100-company test uses mocked source responses and checks terminal output behavior; it is not a live smoke run or a score. No paid API or model is used, so the expected third-party API charge is $0. Network/compute runtime depends on BRREG and company websites and is not estimated from these offline tests.

## Evidence and boundaries

Each claim includes its evidence IDs, retrieval time, and reporting period where relevant. Annual-account facts are matched against the requested organisation number before publication. A deterministic summary refers only to registry-backed facts and lists unknown claim families. Source snapshots are hashed and retained in SQLite; repeated content does not create a false change.

Default sources are BRREG's open entity and roles APIs, its public latest-year annual-account key-figures API, and a BRREG-declared company website. BRREG identifies its open datasets as NLOD-licensed ([dataset/API terms](https://www.brreg.no/en/use-of-data-from-the-bronnoysund-register-centre/datasets-and-api/)); the key-figures API is a preview service with limited coverage and availability ([official API specification](https://raw.githubusercontent.com/brreg/regnskapsregister-api/main/src/main/resources/specification/regnskapsregister.json)). Company website terms apply. There is no general search, social-platform scraping, active-vacancy verification, or dated public-activity collection in this baseline. Missing evidence is labeled rather than inferred.

## Submission readiness

The current Builderr page lists score weights of 50 recall/coverage, 30 precision/evidence, 12 synthesis and 8 UX, with qualification at 65/100 on an official run. The downloadable evaluation contract could not be fetched in this environment, and the required live 100-company report still needs to be run with Builderr's actual batch and network access. Add the submitter's contact details before entry. Offline tests are not a competition score or qualification.

See [ENGINEERING_REPORT.md](ENGINEERING_REPORT.md) for implementation and test details, and [PROJECT_COMPLETION_REPORT.md](PROJECT_COMPLETION_REPORT.md) for the project history, current readiness and prioritized next steps.
