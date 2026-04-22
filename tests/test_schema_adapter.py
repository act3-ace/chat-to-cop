"""Tests for the CoP schema adapter (issue #70).

Covers:
- PassthroughAdapter round-trip equality vs ``record.model_dump()``.
- JsonSchemaAdapter mapping, transforms, construct-time validation.
- build_adapter env-spec parsing.
- End-to-end integration with CoPRESTClient (verifies the wire payload
  actually changes when an adapter is swapped in) per the !88 lesson.

Tests are structured so each one fails when the specific code path it targets
is broken — validated by the mutation-test audit in the MR description.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from chat_to_cop.output.cop_rest_client import CoPRESTClient, SendResult
from chat_to_cop.output.cop_schema import CoPRecord, Track, WriteAuthority
from chat_to_cop.output.schema_adapter import (
    _TRANSFORMS,
    JsonSchemaAdapter,
    PassthroughAdapter,
    SchemaAdapter,
    _resolve_source,
    build_adapter,
)


def _record(**overrides) -> CoPRecord:
    """Build a representative CoPRecord for tests. Overrides take precedence."""
    track = overrides.pop(
        "track",
        Track(
            track_id="TM636",
            track_number="636",
            callsign="ORCA01",
            affiliation="HOSTILE",
        ),
    )
    defaults: dict = {
        "record_type": "track",
        "track": track,
        "write_authority": WriteAuthority.AUTO,
        "extraction_confidence": 0.92,
        "extraction_method": "llm",
        "source_channel": "#c2_coord",
        "source_speaker": "HYDRO_SL",
        "source_message": "Splash one ORCA01 at bullseye 270/50",
        "timestamp": datetime(2026, 4, 22, 14, 0, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return CoPRecord(**defaults)


# ---------------------------------------------------------------------------
# PassthroughAdapter
# ---------------------------------------------------------------------------


class TestPassthroughAdapter:
    def test_map_equals_model_dump(self):
        """Default adapter must produce the same dict as record.model_dump(mode='json').

        This is the contract that makes Passthrough a safe default — existing
        consumers see byte-identical payloads to the pre-#70 behavior.
        """
        adapter = PassthroughAdapter()
        record = _record()
        expected = record.model_dump(mode="json")
        actual = adapter.map(record)
        assert actual == expected

    def test_satisfies_protocol(self):
        """PassthroughAdapter must be a structural SchemaAdapter (runtime-checkable)."""
        assert isinstance(PassthroughAdapter(), SchemaAdapter)

    def test_no_mutation_of_record(self):
        """Adapter must not mutate its input record."""
        adapter = PassthroughAdapter()
        record = _record()
        before = record.model_dump(mode="json")
        _ = adapter.map(record)
        after = record.model_dump(mode="json")
        assert before == after


# ---------------------------------------------------------------------------
# JsonSchemaAdapter — construction-time validation
# ---------------------------------------------------------------------------


_TOY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "trackId": {"type": "string"},
        "classification": {"type": "string"},
        "confidence": {"type": "number"},
        "eventTime": {"type": "string"},
    },
    "required": ["trackId", "confidence"],
}


def _write_schema(tmp_path, schema=None):
    p = tmp_path / "schema.json"
    p.write_text(json.dumps(schema or _TOY_SCHEMA), encoding="utf-8")
    return p


def _write_mapping(tmp_path, mapping):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    return p


class TestJsonSchemaAdapterConstruction:
    def test_valid_mapping_constructs(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "classification": {"source": "track.affiliation", "transform": "lower"},
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        assert adapter.schema_path == schema
        assert adapter.mapping_path == mapping

    def test_unknown_target_fails_at_construct_time(self, tmp_path):
        """A mapping target not in the JSON Schema's properties must fail fast.

        This is the 'typo in target field' guard — the whole point of load-time
        validation is to catch it now rather than silently produce a payload
        the CoP rejects.
        """
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
                "bogusField": "track.track_id",  # not in schema
            },
        )
        with pytest.raises(ValueError, match="not in schema properties"):
            JsonSchemaAdapter(schema, mapping)

    def test_missing_required_field_fails_at_construct_time(self, tmp_path):
        """A schema-required field absent from the mapping must fail fast."""
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                # required 'confidence' is missing
            },
        )
        with pytest.raises(ValueError, match="required schema fields"):
            JsonSchemaAdapter(schema, mapping)

    def test_unknown_transform_fails_at_construct_time(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
                "classification": {"source": "track.affiliation", "transform": "nuke_db"},
            },
        )
        with pytest.raises(ValueError, match="unknown transform"):
            JsonSchemaAdapter(schema, mapping)

    def test_missing_source_key_in_long_form_fails(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": {"transform": "float"},  # no source
            },
        )
        with pytest.raises(ValueError, match="source.*must be a string"):
            JsonSchemaAdapter(schema, mapping)

    def test_schema_file_missing(self, tmp_path):
        mapping = _write_mapping(tmp_path, {"trackId": "track.track_id", "confidence": "extraction_confidence"})
        with pytest.raises(FileNotFoundError):
            JsonSchemaAdapter(tmp_path / "no_such.json", mapping)

    def test_mapping_file_missing(self, tmp_path):
        schema = _write_schema(tmp_path)
        with pytest.raises(FileNotFoundError):
            JsonSchemaAdapter(schema, tmp_path / "no_such.json")

    def test_mapping_not_json_object(self, tmp_path):
        schema = _write_schema(tmp_path)
        p = tmp_path / "mapping.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="Mapping config must be a JSON object"):
            JsonSchemaAdapter(schema, p)

    def test_underscore_keys_in_mapping_are_ignored(self, tmp_path):
        """Allow _description-style annotations in the mapping file."""
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "_description": "this is operator notes",
                "_version": "1",
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        # _description and _version must not be sent in the payload.
        out = adapter.map(_record())
        assert "_description" not in out
        assert "_version" not in out


# ---------------------------------------------------------------------------
# JsonSchemaAdapter — runtime mapping
# ---------------------------------------------------------------------------


class TestJsonSchemaAdapterMapping:
    def test_shorthand_mapping(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert out == {"trackId": "TM636", "confidence": 0.92}

    def test_transform_lower(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "classification": {"source": "track.affiliation", "transform": "lower"},
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert out["classification"] == "hostile"

    def test_transform_upper(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": {"source": "track.track_id", "transform": "upper"},
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert out["trackId"] == "TM636"

    def test_transform_float_and_int(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": {"source": "extraction_confidence", "transform": "float"},
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert isinstance(out["confidence"], float)

    def test_transform_iso_datetime(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
                "eventTime": {"source": "timestamp", "transform": "iso_datetime"},
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        # timestamp already serialized to ISO string by model_dump(mode="json"),
        # so the transform is a pass-through string on this input.
        assert isinstance(out["eventTime"], str)
        assert "2026-04-22" in out["eventTime"]

    def test_missing_optional_source_elides_target(self, tmp_path):
        """If a source path resolves to None and the target is optional, omit it."""
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.track_id",
                "confidence": "extraction_confidence",
                "eventTime": "track.does_not_exist",  # None — optional, should be omitted
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert "eventTime" not in out, "optional target with None source should be elided from payload"

    def test_missing_required_source_still_emits_none(self, tmp_path):
        """If a REQUIRED target's source resolves to None, still emit null.

        Alternative would be to raise; we choose to emit null and let the CoP
        schema reject it. This keeps the pipeline running and surfaces the
        problem as an HTTP 4xx on the far side rather than a Python
        exception at send time.
        """
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": "track.does_not_exist",  # required, resolves to None
                "confidence": "extraction_confidence",
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        out = adapter.map(_record())
        assert "trackId" in out
        assert out["trackId"] is None

    def test_no_mutation_of_record(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {
                "trackId": {"source": "track.track_id", "transform": "upper"},
                "confidence": {"source": "extraction_confidence", "transform": "float"},
            },
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        record = _record()
        before = record.model_dump(mode="json")
        _ = adapter.map(record)
        after = record.model_dump(mode="json")
        assert before == after


# ---------------------------------------------------------------------------
# build_adapter — spec parsing
# ---------------------------------------------------------------------------


class TestBuildAdapter:
    def test_passthrough_default(self):
        assert isinstance(build_adapter("passthrough"), PassthroughAdapter)

    def test_empty_spec_is_passthrough(self):
        assert isinstance(build_adapter(""), PassthroughAdapter)

    def test_jsonschema_spec_constructs(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {"trackId": "track.track_id", "confidence": "extraction_confidence"},
        )
        spec = f"jsonschema:{schema}:{mapping}"
        adapter = build_adapter(spec)
        assert isinstance(adapter, JsonSchemaAdapter)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown schema adapter spec"):
            build_adapter("mystery:foo:bar")

    def test_jsonschema_spec_with_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            build_adapter(f"jsonschema:{tmp_path / 'missing.json'}:{tmp_path / 'also_missing.json'}")


# ---------------------------------------------------------------------------
# Transforms set
# ---------------------------------------------------------------------------


class TestResolveSource:
    """Unit tests for the dotted-path resolver.

    Covers list indexing, dict traversal, and graceful failure modes. These
    are the code paths that the integration tests (``TestJsonSchemaAdapterMapping``)
    exercise indirectly; testing ``_resolve_source`` directly makes the
    boundary explicit and catches bugs in path walking even if the adapter
    happens to produce right answers by accident.
    """

    def test_scalar_dict_path(self):
        data = {"a": {"b": {"c": "leaf"}}}
        assert _resolve_source(data, "a.b.c") == "leaf"

    def test_top_level_key(self):
        assert _resolve_source({"x": 1}, "x") == 1

    def test_missing_key_returns_none(self):
        assert _resolve_source({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        assert _resolve_source({"a": {"b": 1}}, "a.c") is None

    def test_list_index_leaf(self):
        """Numeric segment indexes into a list. This is the code path
        the Phase 0 PR originally claimed to test but did not."""
        assert _resolve_source({"items": ["x", "y", "z"]}, "items.1") == "y"

    def test_list_index_nested_into_dict(self):
        data = {"entities": [{"id": "E1"}, {"id": "E2"}, {"id": "E3"}]}
        assert _resolve_source(data, "entities.2.id") == "E3"

    def test_list_index_out_of_range_returns_none(self):
        assert _resolve_source({"items": ["a", "b"]}, "items.5") is None

    def test_non_numeric_segment_into_list_returns_none(self):
        """A dotted segment that cannot be parsed as int on a list → None."""
        assert _resolve_source({"items": ["a"]}, "items.foo") is None

    def test_traversal_through_scalar_returns_none(self):
        """Walking into a non-container stops with None rather than raising."""
        assert _resolve_source({"a": 42}, "a.b") is None

    def test_empty_path_component_is_none(self):
        """``entities.`` (trailing dot) walks into '' which isn't a key."""
        assert _resolve_source({"entities": []}, "entities.") is None

    def test_none_value_stops_walk(self):
        """Encountering None mid-path yields None for the whole resolution."""
        assert _resolve_source({"a": None}, "a.b") is None


class TestTransforms:
    def test_transforms_list_is_closed(self):
        """No ``eval``, no ``exec``, no lambda loading from config.

        Adding a transform must be a code change, not a config change.
        This test documents the current set.
        """
        assert set(_TRANSFORMS.keys()) == {
            "pass",
            "upper",
            "lower",
            "int",
            "float",
            "iso_datetime",
        }

    def test_pass_is_identity(self):
        assert _TRANSFORMS["pass"](42) == 42
        assert _TRANSFORMS["pass"](None) is None
        assert _TRANSFORMS["pass"]([1, 2]) == [1, 2]

    def test_upper_preserves_non_strings(self):
        assert _TRANSFORMS["upper"](42) == 42  # non-string passed through
        assert _TRANSFORMS["upper"]("foo") == "FOO"

    def test_int_and_float_on_none(self):
        assert _TRANSFORMS["int"](None) is None
        assert _TRANSFORMS["float"](None) is None

    def test_iso_datetime_on_datetime(self):
        dt = datetime(2026, 4, 22, 14, 0, 0, tzinfo=timezone.utc)
        out = _TRANSFORMS["iso_datetime"](dt)
        assert isinstance(out, str)
        assert "2026-04-22" in out


# ---------------------------------------------------------------------------
# End-to-end integration with CoPRESTClient
# (the wire-format check — what actually matters for !88 lesson compliance)
# ---------------------------------------------------------------------------


class TestCoPRESTClientUsesAdapter:
    def test_default_client_uses_passthrough(self):
        """Zero-arg construction gives a Passthrough — wire format unchanged from pre-#70."""
        client = CoPRESTClient(base_url="")
        assert isinstance(client._adapter, PassthroughAdapter)

    def test_explicit_adapter_accepted(self, tmp_path):
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {"trackId": "track.track_id", "confidence": "extraction_confidence"},
        )
        adapter = JsonSchemaAdapter(schema, mapping)
        client = CoPRESTClient(base_url="", adapter=adapter)
        assert client._adapter is adapter

    def test_dry_run_payload_uses_adapter(self, tmp_path):
        """The dry-run path builds ``payload`` from the adapter. If #70 regressed
        to ``record.model_dump(mode="json")``, the payload would have the full
        CoPRecord shape instead of the 2-key JsonSchema output.

        We can't capture the dry-run payload directly (it's only logged), so
        this test asserts the adapter is called by replacing it with a sentinel
        wrapper and checking the call count.
        """
        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {"trackId": "track.track_id", "confidence": "extraction_confidence"},
        )
        adapter = JsonSchemaAdapter(schema, mapping)

        class _Spy:
            def __init__(self, inner: SchemaAdapter):
                self._inner = inner
                self.calls: list[dict] = []

            def map(self, record: CoPRecord) -> dict:
                payload = self._inner.map(record)
                self.calls.append(payload)
                return payload

        spy = _Spy(adapter)
        client = CoPRESTClient(base_url="", adapter=spy)  # dry-run
        record = _record()

        result = asyncio.run(client.send_record(record))
        assert isinstance(result, SendResult)
        assert result.success is True
        assert result.dry_run is True
        assert len(spy.calls) == 1
        assert spy.calls[0] == {"trackId": "TM636", "confidence": 0.92}, (
            "adapter output did not reach send_record — #70 wiring regressed"
        )


# ---------------------------------------------------------------------------
# Config plumbing (!88 lesson — consumer ships with the Field)
# ---------------------------------------------------------------------------


class TestSchemaAdapterConfigPlumbing:
    def test_default_config_yields_passthrough(self):
        """CoPWriterConfig() with no env var must resolve to PassthroughAdapter
        by the time a CoPWriter is built from it.
        """
        from chat_to_cop.config import CoPWriterConfig
        from chat_to_cop.output.cop_writer import CoPWriter

        cfg = CoPWriterConfig()
        writer = CoPWriter.from_config(cfg)
        assert writer._rest_client is not None
        assert isinstance(writer._rest_client._adapter, PassthroughAdapter)

    def test_env_var_jsonschema_reaches_rest_client(self, monkeypatch, tmp_path):
        """Full env → config → CoPWriter.from_config → CoPRESTClient.adapter chain.

        Per the 2026-04-11 !88 lesson: asserts the env var value actually
        lands as a JsonSchemaAdapter inside the real CoPRESTClient, not just
        that the config object has the right string.
        """
        from chat_to_cop.config import CoPWriterConfig
        from chat_to_cop.output.cop_writer import CoPWriter

        schema = _write_schema(tmp_path)
        mapping = _write_mapping(
            tmp_path,
            {"trackId": "track.track_id", "confidence": "extraction_confidence"},
        )
        spec = f"jsonschema:{schema}:{mapping}"
        monkeypatch.setenv("CHAT_TO_COP_SCHEMA_ADAPTER", spec)

        cfg = CoPWriterConfig()
        assert cfg.schema_adapter == spec

        writer = CoPWriter.from_config(cfg)
        adapter = writer._rest_client._adapter
        assert isinstance(adapter, JsonSchemaAdapter), (
            f"env var did not reach CoPRESTClient — got {type(adapter).__name__}. !88 plumbing regression."
        )
        # And verify it is functionally wired — not just the right class.
        out = adapter.map(_record())
        assert out == {"trackId": "TM636", "confidence": 0.92}


# ---------------------------------------------------------------------------
# Protocol structural check (smoke test)
# ---------------------------------------------------------------------------


def test_protocol_is_runtime_checkable():
    """SchemaAdapter must be usable with isinstance() checks at runtime.

    Tests that happen to import Pydantic BaseModel for other reasons also
    need to not accidentally match — guards against widening the protocol
    to the point that it trivially matches anything.
    """

    class _NoMap(BaseModel):
        pass

    assert not isinstance(_NoMap(), SchemaAdapter)
    assert isinstance(PassthroughAdapter(), SchemaAdapter)
