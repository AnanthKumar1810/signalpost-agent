# BRREG accounts failure diagnosis: 987441623

Checked 20 October 2026 against the live BRREG endpoints.

The requested accounts URL returned HTTP 500 on three consecutive `curl.exe -sS -i --max-time 20` requests. All three response bodies gave the same explanation: `Regnskapet inneholder en oppstillingsplan som ikke er stottet (IDEELL)`. This is a deterministic unsupported-accounting-plan response, not a transient generic server error.

The Enhetsregisteret lookup for `987441623` returned legal name `STIFTELSEN VELFERDSSAMBANDET TELENOR BERGEN` and `organisasjonsform.kode` `STI` (foundation). The API error identifies the particular excluded feature as accounting presentation plan `IDEELL`; the classifier keys on that explicit response text, not on `STI`, because a legal form by itself does not prove which statement plan BRREG received. BRREG's API catalogue says the public key-figures endpoint covers entities following the ordinary presentation plan and excludes bank and insurance data and consolidated group figures. See the [BRREG API catalogue](https://github.com/brreg/openAPI/blob/master/specs/API-catalog.ttl) and its [accounts API repository documentation](https://github.com/brreg/regnskapsregister-api/blob/main/docs/for-devs.md).

`signalpost_agent.py` now reports this exact response as `financials: not_applicable`, reason `BRREG reports an unsupported IDEELL accounting presentation plan for this entity`. It preserves the HTTP response as diagnostic error evidence. Other HTTP 500 responses, including ones without the explicit unsupported-plan message, remain `failed`.

Verification: a live one-company CLI run returned one verified identity and `financials: not_applicable`; the full test suite passed 107/107 tests. Regression tests cover both the exact IDEELL response and an unrelated generic HTTP 500.
