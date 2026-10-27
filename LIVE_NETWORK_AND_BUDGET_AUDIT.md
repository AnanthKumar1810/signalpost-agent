# Live network and request-budget audit — 27 October 2026

## Question and setup

The earlier 10-company pilot hit local caps of 120 and 160 requests. Its first attempt also ran in a shell where `data.brreg.no` could not be resolved. These are separate conditions. This audit reran the same ten organisation numbers from `outputs/benchmark-homepage-10-input.json` with network-enabled execution and a local cap of 300 outbound calls. No API keys were written to the repository or report.

Command used for both new runs (the second changed only the output and state paths after the scheduling fix):

```powershell
python signalpost_agent.py --input outputs/benchmark-homepage-10-input.json --output outputs/network-enabled-10-300-scheduled.jsonl --state outputs/network-enabled-10-300-scheduled.sqlite3 --timeout 8 --workers 4 --deadline-seconds 720 --finalization-reserve-seconds 30 --max-outbound-requests 300 --nav-jobs --nav-pages 10 --nav-details 20 --wikidata-websites --tinyfish-websites --tavily-websites
```

| Measured item | Before schedule fix | After schedule fix |
| --- | ---: | ---: |
| Input/output profiles | 10/10 | 10/10 |
| Verified BRREG identities | 10 | 10 |
| Permitted outbound requests | 73/300 | 201/300 |
| TinyFish target companies / candidates / verified sites | 1 / 2 / 0 | 9 / 25 / 0 |
| Tavily target companies / candidates / verified sites | 0 / 0 / 0 | 9 / 24 / 0 |
| Verified website profiles | 1 | 1 |
| Available jobs profiles | 1 | 1 |
| Available public-activity profiles | 0 | 0 |
| Wall time | 101.8 seconds | 423.2 seconds |

Sources: `outputs/network-enabled-10-300.jsonl.report.json` and `outputs/network-enabled-10-300-scheduled.jsonl.report.json`. Generated JSONL files remain local because `outputs/` is ignored; the counts above are copied from the run reports. These are practice runs, not an official Builderr score.

## What the runs establish

The network-enabled environment reached BRREG for all ten companies. Network access was an execution-environment setting, not a change needed in BRREG or in the company-number matcher.

The first new run left 227 of 300 requests unused while TinyFish was deadline-limited and Tavily never started. The cause was the old phase formula: on a 720-second batch with a 30-second finalization reserve, it reserved 660 seconds for NAV and left about 30 seconds for all website discovery. The fix reserves 20% of the actual collection window for NAV (138 seconds in this run) and, when enabled, 10% for summaries. Source discovery now has meaningful time. The second run used 201 calls and completed TinyFish and Tavily for all nine companies lacking a verified site.

More search did not produce more verified sites in this sample. The 49 candidates were leads, not verified facts. No TinyFish or Tavily candidate passed the current company-owned website gate. Raising the request cap again would not explain or fix that outcome; source quality, exact-entity evidence, and source rights now matter more. The NAV scan remained partial because this command deliberately limited it to ten pages and 20 details.

One output defect surfaced: failed search candidates changed a previously `ambiguous` BRREG-listed website result to `not_available`. The search-result handling now preserves the original claim and records failed candidate discovery separately. A focused live recheck of organisation `980383318` used 26 outbound calls, searched three TinyFish and three Tavily candidates, and retained `web_presence: ambiguous` with no invented site. A regression test covers the status preservation.

The ten-company run logged one BRREG accounts HTTP 500 for `987441623`. Its response identifies an unsupported `IDEELL` accounting plan; the financials claim was correctly marked `not_applicable`, with no financial value guessed. It remains in the run's error log for auditability.

## Third-party pages and official limit

Builderr's [source policy](https://builderr.ai/starter-briefs/signalpost-sources.md) permits public pages whose terms allow the submitted access pattern, provided a profile resolves to the exact legal entity and each fact has evidence. A directory page can therefore be an attributed third-party source, but it is not proof of a company-owned website. Some candidate directories explicitly prohibit automated systematic collection; for example, [Proff's rights page](https://www.proff.no/info/rettigheter/) and [Firmaportalen's terms](https://firmaportalen.no/vilkaar). The agent currently rejects directories as company-owned website candidates and does not publish directory facts. Accepting any directory automatically would mislabel the source and risk publishing duplicates or wrong-company data.

The older challenge brief named 2,000 outbound calls. The [current Builderr challenge page](https://builderr.ai/challenges/signalpost) describes a fixed official time and resource budget without a numeric request cap. The CLI still defaults to and caps at 2,000 as a conservative implementation limit. The 120 and 160 caps were local pilot settings; `--max-outbound-requests` raised this audit's cap to 300. The official evaluator's numeric ceiling needs confirmation before raising the CLI hard maximum.

The run report's `requests` and `outbound_request_budget.used` are the permitted outbound count. `profile_reported_requests` includes per-profile attempts, including requests denied after a cap is reached; it can exceed the permitted count without an actual over-budget call.
