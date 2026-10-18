# Reference implementation review and isolated checks

Reviewed 18 October 2026 from shallow Git clones pinned to the commits below. These are separate projects and were not added as runtime dependencies to Signalpost.

| Reference | Inspected revision | Architecture and useful idea | Isolated check and result | Decision for Signalpost |
|---|---|---|---|---|
| [Fire Enrich](https://github.com/mendableai/fire-enrich) | `94e061dc5bab94e5838cceb8b3d5e6af329d2d50` | Next.js enrichment app that combines Firecrawl extraction with OpenAI synthesis. Its setup requires Firecrawl and OpenAI credentials. It enriches email/domain leads, so brand/domain identity is not enough for this challenge's legal-entity key. | `npm test -- --runInBand` → not runnable: package has no `test` script. `package.json` exposes dev/build/start/lint only; dependencies were not installed, no API keys were used. | Reuse source-specialized extraction and attribution ideas. Do not introduce per-company paid crawling/LLM orchestration as the default batch path. |
| [Company Research Agent](https://github.com/guy-hartstein/company-research-agent) | `80ed5040195fd8aad7225dfc3c2a80ecbaa336c5` | LangGraph research workflow with analyzers, collection, curation, briefing and editing; uses web research/LLM services and is aimed at rich, interactive reports. | `python -m compileall -q .` passed. No test files were present in the repository tree; no provider credentials were configured, so no graph/API run was attempted. | Its planner/curator separation is useful for a later opt-in deep-research mode. It is too costly and heavy for each company in the bounded official batch. |
| [Dataforge / FORGE](https://github.com/Nuclear-Marmalade/dataforge) | `e9059821f090f217cb708317869bcc06611fb45b` | Python enrichment CLI with web extraction, optional AI, database adapters and checkpoint/recovery code. It includes a pytest suite and optional development dependencies. | `python -m compileall -q forge` passed. `python -m pytest forge/tests/test_smoke.py -q` could not start because pytest is absent. Isolated dependency installation was attempted but package-index/temp access was denied by this managed environment; no test result is claimed. | Adopt bounded deterministic extraction, state snapshots and safe recovery patterns. Keep its database/optional-AI stack out of the minimal Signalpost runtime. |
| [python-brreg](https://github.com/crdbrd/python-brreg) | `013575b8d83c22bbf14fd8acb67ae2cd30b554f5` | Typed Python client for BRREG open APIs, backed by Pydantic and an HTTP client. This is the closest source-specific reference. | The repository's pytest command could not start because pytest is absent and isolated dependency installation was blocked. A direct isolated smoke of its `OrganisasjonsnummerValidator` passed for both `923 609 016` and integer `923609016`, normalizing both to `923609016`. | The existing Signalpost adapter keeps the standard-library-only deployment. Retain org-number-first retrieval and the tested normalization/checksum gate; use the official schema as authority. |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | `94b67888ca8b335ff2121423a50e06dd27661922` | Async browser-based crawler with extraction strategies, caching, markdown and LLM integrations. The full runtime depends on browser tooling and a broad dependency set. | `python -m compileall -q crawl4ai` passed. `python -m pytest tests/test_config_selection.py -q` could not start because pytest is absent. No browser install or live crawl was attempted. | Use only the small amount of same-domain, robots-checked retrieval needed here. Browser rendering is deferred unless the checked sample shows useful facts are inaccessible in static HTML. |

The commands actually run were:

```text
python -m compileall -q .                    # Company Research Agent — passed
python -m compileall -q forge                # Dataforge — passed
python -m compileall -q crawl4ai             # Crawl4AI — passed
python -m pytest forge/tests/test_smoke.py -q # blocked: pytest unavailable
python -m pytest tests/test_config_selection.py -q # blocked: pytest unavailable
python -m pytest tests/enhetsregisteret/test_types.py -q # blocked: pytest unavailable
npm test -- --runInBand                      # blocked: no test script
```

These checks compare repository architecture and execution prerequisites; they are not a head-to-head accuracy or performance benchmark. Only the BRREG number-validator smoke ran behaviorally, with no network request. Full isolated suites were not run because this Windows environment has no pytest; the attempt to install it in an isolated target failed on restricted temporary/package-index access. This limitation is reported rather than filled with guessed metrics. The current Signalpost suite, by contrast, passes 59/59 tests and its 100-company live batch is recorded in `QA_TEST_REPORT.md`.
