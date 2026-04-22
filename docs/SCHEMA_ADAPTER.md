# CoP Schema Adapter — operator guide

## What this is

A config-boundary layer between `CoPRecord` and the wire payload POSTed to the
CoP REST API. Lets the pipeline ship without the contractor's CoP schema and
then adopt one at the event in ~30 minutes by editing two files and flipping
one env var. Phase 0 of the plan in issue #70.

## Default (passthrough)

No action required. `CHAT_TO_COP_SCHEMA_ADAPTER` unset or `"passthrough"` →
the wire payload is `CoPRecord.model_dump(mode="json")` — exactly the
pre-#70 behavior. This is what the pipeline ships as on MASH day 1 if
nothing has been configured.

## Switching to a real CoP schema at-event

The contractor hands over a JSON Schema for their CoP API. In the 30 minutes
before the exercise begins, an operator does the following:

### 1. Save the contractor's JSON Schema

```bash
cp /path/to/cop_schema.json /srv/chat-to-cop/schemas/cop_schema.json
```

The schema must have top-level `properties` (standard JSON Schema).
`required` is respected at mapping-load time (every required target must be
mapped, or startup fails loudly).

### 2. Write the mapping config

Target-keyed JSON. Two forms: shorthand (string source path) and long form
(dict with `source` and optional `transform`):

```json
{
  "_description": "Mapping from our CoPRecord to the MASH CoP schema, 2026-04-22",
  "trackId":        "track.track_id",
  "trackNumber":    {"source": "track.track_number", "transform": "upper"},
  "classification": {"source": "track.affiliation",  "transform": "lower"},
  "confidence":     "extraction_confidence",
  "eventTime":      {"source": "timestamp",          "transform": "iso_datetime"}
}
```

Keys starting with `_` are annotations and are stripped at load time.

**Transforms** are a closed set (no code execution):
- `pass` — identity (default when omitted)
- `upper` / `lower` — string case
- `int` / `float` — numeric casts, `None` passes through
- `iso_datetime` — `datetime` → ISO-format string

Save as `/srv/chat-to-cop/schemas/cop_mapping.json`.

### 3. Point the pipeline at the two files

```bash
export CHAT_TO_COP_SCHEMA_ADAPTER=jsonschema:/srv/chat-to-cop/schemas/cop_schema.json:/srv/chat-to-cop/schemas/cop_mapping.json
```

Restart the pipeline (`docker compose restart chat-to-cop` or equivalent).
At startup the adapter logs the number of schema fields, mappings, and
required fields it loaded. If the mapping is malformed (unknown target,
missing required, unknown transform, missing file) **the pipeline fails
to start with a clear error** — the problem surfaces in 30 seconds, not 30
minutes into the exercise.

### 4. Verify before the exercise starts

Run one message through replay in dry-run mode:

```bash
python -m chat_to_cop.replay data/chat/test.zip --num-ctx 8192
```

In dry-run mode the adapter runs but no HTTP request is sent; `CoPRESTClient`
logs the payload shape at trace level. Confirm the wire format matches the
contractor's example payload.

## Source-path syntax

Source paths are dotted strings walked against `CoPRecord.model_dump(mode="json")`:

```
track.track_id           → dict["track"]["track_id"]
track.callsign           → dict["track"]["callsign"]
extraction_confidence    → dict["extraction_confidence"]
timestamp                → dict["timestamp"]   (already an ISO string)
source_channel           → dict["source_channel"]
```

Numeric segments index into lists:

```
entities.0.callsign      → dict["entities"][0]["callsign"]
```

A path that doesn't resolve (missing key, out-of-range index, wrong type)
yields `None`. For optional targets the resulting key is elided from the
payload. For required targets, `null` is emitted so the CoP's own
validation can reject it with a clear HTTP 4xx.

## Phase 1 and Phase 2

Phase 0 (this guide) covers the static-mapping case. Later phases, tracked
in issues #71 and #72:

- **#71 — adapt-schema CLI**: ingest the contractor's schema and a sample
  payload, ask an LLM to propose a mapping config, write it out for review.
  Turns step 2 above from "20 minutes of hand-writing" into "2 minutes
  of review."
- **#72 — hot-reload + adaptive learner**: watch the mapping file for
  changes; collect first-hour diff between emitted payloads and CoP
  complaints; propose mapping refinements in the background while the
  exercise runs.

## Troubleshooting

**"Mapping target 'X' is not in schema properties"** at startup.
The mapping names a field that does not exist in the JSON Schema's
top-level `properties`. Typo, or the target field lives in a nested
object that Phase 0 does not support (widening planned in Phase 1).

**"Mapping is missing entries for required schema fields"** at startup.
One or more fields in the schema's `required` list have no mapping entry.
Add them or remove them from `required`.

**"Unknown transform"** at startup. The transform name is not in the
closed set above. Add a mapping entry using a supported transform, or
preprocess the value upstream.

**HTTP 4xx from the CoP at runtime** with otherwise clean startup.
The mapping loaded successfully but the wire payload is semantically wrong
(e.g., a string where the CoP expects a number). Use the CoP's error
response to identify the field; adjust the transform or source path in
the mapping file, restart.

## Related

- Issue #70 (Phase 0, this MR)
- Issue #71 (Phase 1, at-event CLI)
- Issue #72 (Phase 2, hot-reload + adaptive learner)
- `tests/test_schema_adapter.py` — Protocol conformance, transforms,
  construct-time validation, runtime mapping, and end-to-end integration
  with `CoPRESTClient`.
