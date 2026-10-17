# Signalpost evidence-first agent

Python 3.10 or newer; standard library only. Each input organization number yields one JSONL result. Organization-number validation gates all research. The official Brønnøysund Register supplies the identity anchor, registered company facts and roles. If a registered website is listed, the runner makes one bounded HTTPS request and publishes its descriptive facts only when the page includes the exact legal name or organization number.

## Run

```powershell
python signalpost_agent.py --input smoke.json --output outputs/smoke.jsonl --state outputs/state.sqlite3
```

The same command accepts a JSON array, a JSON object with `organisation_number`, JSONL objects with `organisation_number`, or one organization number per text line. `--no-website` runs the registry-only baseline. Use `python -m unittest -v` for the offline tests. No external package, model key, search credential or paid service is required. The local 5-company input is a reproducibility sample, not the competition's requested 100-company smoke result.

## Output

One envelope per input, with identity anchor, typed claim-family statuses, claim facts, evidence references, source snapshot hashes, errors and request/runtime/cost fields. SQLite snapshots are keyed by organization, family and content hash; a separate current pointer detects material changes and an identical repeat does not report a false change. The command also writes a machine-readable run report and a responsive local HTML profile browser. Run output is overwritten for each invocation.

## Sources and boundaries

Default outbound requests go only to the official BRREG JSON API and a company website explicitly listed by BRREG. The website request checks `robots.txt`, requires public IP resolution, permits only same-host HTTPS redirects, and is bounded to one page. The crawler does not discover unrelated domains, bypass access controls, or use social platforms. Unsupported or incomplete data is marked unavailable, ambiguous or failed. It does not crawl internal pages or extract account PDF content, verified vacancies, or dated news. Before expanding collection, check source terms and competition permitted-source guidance.
