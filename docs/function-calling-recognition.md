# Recognition Function Calling

## Contract and ownership

`material_output_schema` holds the shared field declarations and ownership;
`material_recognition_schema` exports their JSON Schema, reusing parser-required
sets, enums and workload dataclass defaults. The stage projection excludes the
optional historical root and item estimates. Those fields remain valid in the
historical model; independent workload estimation owns duration and policy.

The TJU tool projection only changes nullable object unions to equivalent
`anyOf` branches and removes annotation-only schema metadata. Prompt-side
semantics are derived from the same declarations, feature metadata and policy
registry. Responses are never rewritten to match types or enums.

## Production route

The default TJU adapter uses `material_recognition_result` with `tool_choice=auto`.
Forced function choice was not reliable on the verified endpoint. Other
providers, injected legacy callers and explicit `recognition_tools=False` retain
the existing content route. Other agents and duration estimation are unchanged.

Exactly one target call and string arguments are required. Arguments pass strict
JSON, the projected schema, full formal schema/parser, actionability, source
support and existing factual guards. A scoped semantic guard rejects positive
local-output evidence mislabeled as external submission/attachment; it does not
classify unknown text or remap enums.

## Bounded failures

Normal and semantic repair use the same tool/schema/guide generator. There is
one recognition repair slot, also used for a missing/wrong/multiple tool call.
No business validation failure causes repeated switching to content JSON.
Pure JSON syntax failure in semantic repair may use the existing single
serialization attempt, on the same function channel and with semantic equality
checks. Repeated invalid candidates cannot become a published function result.
Old content capability and existing safe failure behavior remain available.

## Evaluation

The ten original synthetic inputs are in `scripts/fc_fixtures.py`. Development
acceptance checks scope, completion and grounded features jointly; scope need
not list every action. Feature evidence can retain entity details, but evidence
alone cannot establish that a workload action was recognized. Gold-based
coverage repair is a development quality check, not an extra production NLP
validator. Formal local-output contradictions are checked in production too.

Opt-in API runs use the configured endpoint, bounded requests, temporary profiles and
safe structural/usage reports under ignored `artifacts/evaluation`. No raw model
responses, credentials, official plans or user SQLite files belong in commits.

## Validation scope

See [public validation summary](validation.md) for current evidence and limitations.
Bounded recovery does not guarantee first-try tool choice or semantic accuracy.
Production does not use fixture gold or silently coerce invalid output.
