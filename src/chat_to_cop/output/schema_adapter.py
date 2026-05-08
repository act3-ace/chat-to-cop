"""Schema adapter layer for the CoP writer.

Phase 0 of the at-event schema-absorption plan (issue #70). Lets the pipeline
ship without the contractor's CoP schema, then adopt one at the event in ~30
minutes by dropping a mapping config onto disk and flipping an env var.

**Why this layer exists.** The contractor's CoP schema is not expected before
MASH. Two prior alternatives were weak: (a) wait for the schema, block on a
hard external dependency; (b) hand-edit Python at the event under pressure.
This layer makes the CoP-payload shape a config boundary — ``PassthroughAdapter``
is the default (writes ``CoPRecord.model_dump()`` unchanged, preserving prior
behavior), and ``JsonSchemaAdapter`` transforms the record into the target
schema's expected shape from a declarative mapping file.

**Integration point.** ``CoPRESTClient.send_record`` used to serialize the
record with ``record.model_dump(mode="json")``. It now delegates to the
adapter's ``.map(record)``. The rest of the pipeline (``CoPWriter``,
``CoPUpdate`` → ``CoPRecord`` tier logic, retry, pause queue) is unchanged.

**Why this adapter operates on CoPRecord, not CoPUpdate.** The original
acceptance criteria in #70 named ``map(update: CoPUpdate)``. In implementation
it became clear that ``CoPRecord`` is the right boundary: (a) ``CoPWriter``'s
tier logic (auto/flagged/human) has already run by the time a record reaches
the wire, and the CoP's target fields care about that authority metadata;
(b) the CoP receives tracks and battle effects, not raw extractions, and
``CoPRecord`` already models that split. Keeping the adapter above the tier
logic would force every mapping config to re-implement authority decisions.
``CoPUpdate`` → ``CoPRecord`` stays in ``cop_schema.py`` where it belongs.
Tests assert round-trip equivalence against ``record.model_dump()``.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from chat_to_cop.output.cop_schema import CoPRecord

# ---------------------------------------------------------------------------
# Protocol + passthrough
# ---------------------------------------------------------------------------


@runtime_checkable
class SchemaAdapter(Protocol):
    """Convert a CoPRecord into the dict shape the target CoP API expects.

    Implementations must be side-effect-free and thread-safe enough for
    async callers — they are invoked on every outbound record.
    """

    def map(self, record: CoPRecord) -> dict[str, Any]: ...


class PassthroughAdapter:
    """Default adapter: returns the record's own ``model_dump(mode='json')``.

    Use when there is no external schema to translate into — i.e., local-only
    mode, dry-run, or any deployment that consumes chat-to-cop's own payload
    shape directly. Ships as the default so the pipeline works day one with
    no schema config.
    """

    def map(self, record: CoPRecord) -> dict[str, Any]:
        return record.model_dump(mode="json")


# ---------------------------------------------------------------------------
# JsonSchemaAdapter: mapping-config driven
# ---------------------------------------------------------------------------


# Intentionally small, explicit set. No `eval`, no lambda loading. Adding a
# transform is a code change reviewed in a PR, not a config-file change that
# slips a code-execution path into the CoP writer.
_TRANSFORMS: dict[str, Any] = {
    "pass": lambda v: v,
    "upper": lambda v: v.upper() if isinstance(v, str) else v,
    "lower": lambda v: v.lower() if isinstance(v, str) else v,
    "int": lambda v: int(v) if v is not None else None,
    "float": lambda v: float(v) if v is not None else None,
    "iso_datetime": lambda v: v.isoformat() if isinstance(v, (datetime, date)) else (str(v) if v is not None else None),
}


def _parse_mapping_entry(entry: Any, target: str) -> tuple[str, str]:
    """Normalize a mapping entry to (source_path, transform_name).

    Accepts either a shorthand string (``"track.track_id"``) or a long form
    dict (``{"source": "track.track_id", "transform": "upper"}``). Raises
    ValueError with a helpful message on malformed entries so mis-configs
    fail at construct time, not at first write.
    """
    if isinstance(entry, str):
        return entry, "pass"
    if isinstance(entry, dict):
        source = entry.get("source")
        if not isinstance(source, str):
            raise ValueError(f"mapping[{target!r}]: 'source' must be a string, got {source!r}")
        transform = entry.get("transform", "pass")
        if transform not in _TRANSFORMS:
            raise ValueError(f"mapping[{target!r}]: unknown transform {transform!r}. Allowed: {sorted(_TRANSFORMS)}")
        return source, transform
    raise ValueError(
        f"mapping[{target!r}]: entry must be a string (shorthand) or object "
        f"with 'source' + optional 'transform', got {type(entry).__name__}"
    )


def _resolve_source(record_dict: dict[str, Any], path: str) -> Any:
    """Walk a dotted path through a dict, returning None on any miss.

    ``entities.0.callsign`` walks dict["entities"][0]["callsign"]. Numeric
    segments index into lists; string segments index into dicts. Missing
    keys, out-of-range indices, and wrong-type traversals all return None
    so the mapping can proceed without raising.
    """
    current: Any = record_dict
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _collect_schema_properties(schema: dict[str, Any]) -> set[str]:
    """Flatten a JSON Schema's top-level properties into a set of dotted paths.

    Phase 0 supports only top-level target fields — nested target paths are
    deferred to Phase 1 because the contractor's schema shape is still TBD
    and over-designing now invites rework. If a real schema needs
    ``position.latitude``-style nesting, widen this function and the
    write path accordingly.
    """
    props = schema.get("properties")
    if not isinstance(props, dict):
        return set()
    return set(props.keys())


def _valid_record_top_level_fields() -> set[str]:
    """Return the set of top-level field names on ``CoPRecord``.

    Used to warn at load time when a mapping source path starts with a
    segment that could not be a CoPRecord field (typo class). This is a
    permissive check — subsequent segments (e.g., ``track.track_nmbr`` —
    typo in ``track_number``) aren't validated here because walking nested
    Pydantic models from a string path is more complex than is warranted
    for Phase 0.
    """
    return set(CoPRecord.model_fields.keys())


def _collect_required(schema: dict[str, Any]) -> set[str]:
    required = schema.get("required")
    if not isinstance(required, list):
        return set()
    return {r for r in required if isinstance(r, str)}


class JsonSchemaAdapter:
    """Maps ``CoPRecord`` → target dict using a JSON Schema and a mapping config.

    **Mapping config shape** (target-keyed):

    .. code-block:: json

        {
          "trackId": "track.track_id",
          "classification": {"source": "track.affiliation", "transform": "lower"},
          "confidence": "extraction_confidence"
        }

    Keys must appear in the JSON Schema's top-level ``properties``. Every
    entry in ``required`` must have a mapping. Both invariants are checked
    at construct time — a typo or missing required field fails fast at
    startup, not silently on the first outbound record.

    **Transforms** are a small closed set: ``upper``, ``lower``, ``int``,
    ``float``, ``iso_datetime``, ``pass``. No user code is executed.

    **Missing sources** are elided from the output dict. If a source path
    resolves to None, the target key is simply not emitted — the CoP schema
    should then use its own default, or validate as absent. Required-field
    enforcement is at mapping load time (every required key must have a
    mapping entry), not at per-record send time; the latter is the CoP's
    contract to enforce.
    """

    def __init__(self, schema_path: str | Path, mapping_path: str | Path) -> None:
        self.schema_path = Path(schema_path)
        self.mapping_path = Path(mapping_path)

        if not self.schema_path.exists():
            raise FileNotFoundError(f"JSON Schema not found: {self.schema_path}")
        if not self.mapping_path.exists():
            raise FileNotFoundError(f"Mapping config not found: {self.mapping_path}")

        self._schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        raw_mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        if not isinstance(raw_mapping, dict):
            raise ValueError(
                f"Mapping config must be a JSON object, got {type(raw_mapping).__name__} at {self.mapping_path}"
            )

        schema_fields = _collect_schema_properties(self._schema)
        required_fields = _collect_required(self._schema)
        record_fields = _valid_record_top_level_fields()

        # Gotcha #3 from the post-merge review: schema without a top-level
        # 'properties' block (e.g., one built from $ref, allOf, anyOf) would
        # silently skip target-field validation. Warn so operators know.
        if not schema_fields:
            logger.warning(
                "Schema at {} has no top-level 'properties' — target-field "
                "validation skipped. Any mapping target will be accepted. "
                "Phase 0 does not flatten $ref/allOf/anyOf; widen "
                "_collect_schema_properties if your real schema uses composition.",
                self.schema_path,
            )

        # Normalize + validate. Target fields must exist in the schema's
        # top-level properties; unknown targets indicate the operator
        # intended a field that the schema does not advertise — almost
        # always a typo.
        normalized: dict[str, tuple[str, str]] = {}
        for target, entry in raw_mapping.items():
            if target.startswith("_"):
                # Ignore _description and similar annotations.
                continue
            if schema_fields and target not in schema_fields:
                raise ValueError(
                    f"Mapping target {target!r} is not in schema properties at "
                    f"{self.schema_path}. Known properties: {sorted(schema_fields)}"
                )
            source, transform = _parse_mapping_entry(entry, target)
            normalized[target] = (source, transform)

            # Gotcha #4 from the post-merge review: source paths whose first
            # segment isn't a CoPRecord field are almost always typos. Warn
            # — don't raise, because a source path *could* legitimately
            # traverse a metadata dict or a future CoPRecord extension we
            # don't know about yet.
            first_segment = source.split(".", 1)[0]
            if first_segment and first_segment not in record_fields:
                logger.warning(
                    "Mapping source path {!r} for target {!r} starts with "
                    "{!r}, which is not a CoPRecord top-level field. Typo? "
                    "Known fields: {}",
                    source,
                    target,
                    first_segment,
                    sorted(record_fields),
                )

        missing_required = required_fields - normalized.keys()
        if missing_required:
            raise ValueError(
                f"Mapping is missing entries for required schema fields: "
                f"{sorted(missing_required)}. Add them to {self.mapping_path} or "
                f"remove them from the schema's 'required' list."
            )

        self._mapping = normalized
        self._required = required_fields
        logger.info(
            "JsonSchemaAdapter loaded: {} schema fields, {} mappings, {} required",
            len(schema_fields),
            len(normalized),
            len(required_fields),
        )

    def map(self, record: CoPRecord) -> dict[str, Any]:
        record_dict = record.model_dump(mode="json")
        out: dict[str, Any] = {}
        for target, (source_path, transform_name) in self._mapping.items():
            value = _resolve_source(record_dict, source_path)
            if value is None and target not in self._required:
                # Optional field with missing source — omit from output.
                continue
            value = _TRANSFORMS[transform_name](value)
            out[target] = value
        return out


# ---------------------------------------------------------------------------
# Hot-reload wrapper (issue #72)
# ---------------------------------------------------------------------------


class HotReloadAdapter:
    """Wraps a JsonSchemaAdapter with mtime-based auto-reload.

    Checks the mapping file's mtime at most once every ``check_interval``
    seconds. On change, re-validates the new mapping against the schema
    and swaps atomically. If the new mapping is invalid, keeps the old
    one and logs a warning.
    """

    def __init__(
        self,
        schema_path: str | Path,
        mapping_path: str | Path,
        check_interval: float = 30.0,
    ) -> None:
        self._schema_path = Path(schema_path)
        self._mapping_path = Path(mapping_path)
        self._check_interval = check_interval
        self._inner = JsonSchemaAdapter(schema_path, mapping_path)
        self._last_mtime = self._mapping_path.stat().st_mtime
        self._last_check = time.monotonic()

    def _maybe_reload(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return
        self._last_check = now
        try:
            current_mtime = self._mapping_path.stat().st_mtime
        except OSError:
            return
        if current_mtime == self._last_mtime:
            return
        logger.info("Mapping file changed, reloading: {}", self._mapping_path)
        try:
            new_adapter = JsonSchemaAdapter(self._schema_path, self._mapping_path)
            self._inner = new_adapter
            self._last_mtime = current_mtime
            logger.info("Mapping reloaded successfully")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(
                "New mapping at {} is invalid ({}), keeping previous version",
                self._mapping_path,
                e,
            )

    def map(self, record: CoPRecord) -> dict[str, Any]:
        self._maybe_reload()
        return self._inner.map(record)


# ---------------------------------------------------------------------------
# Factory (consumed by config + CoPRESTClient)
# ---------------------------------------------------------------------------


def build_adapter(spec: str) -> SchemaAdapter:
    """Parse a ``CHAT_TO_COP_SCHEMA_ADAPTER`` spec into an adapter instance.

    Accepted forms:

    - ``"passthrough"`` (default): ``PassthroughAdapter()``.
    - ``"jsonschema:/path/to/schema.json:/path/to/mapping.json"``: construct a
      ``JsonSchemaAdapter`` from the two files. Paths may be absolute or
      relative to the current working directory.

    Raises ``ValueError`` on unrecognized kind, ``FileNotFoundError`` on
    missing files. The config plumbing test in ``tests/test_config.py``
    verifies the env var actually reaches the real ``CoPRESTClient`` — per
    the 2026-04-11 !88 lesson.
    """
    spec = spec.strip()
    if spec == "" or spec == "passthrough":
        return PassthroughAdapter()
    if spec.startswith("jsonschema:"):
        # Windows-friendly: accept either ``jsonschema:schema:mapping`` or
        # ``jsonschema:C:\path\schema.json:C:\path\mapping.json``. rsplit
        # with maxsplit=1 is wrong because Windows paths have a colon after
        # the drive letter. Use explicit "schema="/"mapping=" for the
        # unambiguous case if we ever need it; for now, the two-path form
        # is split on the FIRST colon after the kind and the LAST colon
        # before the mapping, assuming mapping has no colon in its path
        # except the optional drive letter. Concretely: split off "jsonschema:"
        # then split the rest on ":" with max 2 parts if no Windows drive,
        # or handle drive letters explicitly.
        payload = spec[len("jsonschema:") :]
        schema_path, mapping_path = _split_two_paths(payload)
        return HotReloadAdapter(schema_path=schema_path, mapping_path=mapping_path)
    raise ValueError(
        f"Unknown schema adapter spec: {spec!r}. Expected 'passthrough' or 'jsonschema:<schema_path>:<mapping_path>'."
    )


def _split_two_paths(payload: str) -> tuple[str, str]:
    """Split ``<schema_path>:<mapping_path>``, handling Windows drive letters.

    A Windows absolute path contains a colon after the drive letter
    (``C:\\path``). Naive ``.split(":")`` would slice it incorrectly. This
    helper walks colons and treats a single-character segment followed by
    a backslash or forward slash as a drive letter, not a separator.
    """
    parts = payload.split(":")
    # Rebuild drive-letter paths: a length-1 part immediately followed by a
    # path-looking segment is a drive letter.
    rebuilt: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if len(p) == 1 and p.isalpha() and i + 1 < len(parts) and parts[i + 1].startswith(("\\", "/")):
            rebuilt.append(f"{p}:{parts[i + 1]}")
            i += 2
        else:
            rebuilt.append(p)
            i += 1
    if len(rebuilt) != 2:
        raise ValueError(
            f"jsonschema spec must be 'jsonschema:<schema_path>:<mapping_path>'; "
            f"got {len(rebuilt)} path segment(s): {rebuilt}"
        )
    return rebuilt[0], rebuilt[1]
