"""CoP REST API writer: pushes updates to the actual CoP database.

Forwards CoPUpdates from the world state store to the Track Manager
and SmartPack Manager REST APIs.

Design:
- Optimistic writes (push immediately, don't wait for validation)
- Idempotent (handle duplicate messages from chat + STT gracefully)
- Schema pending from Sarah Bowman (711 HPW) -- current implementation
  writes to local store only; CoP forwarding activated when API is available
- Tiered write authority (RAI requirement):
    AUTO:    confidence >= auto_threshold, low-risk type -> write immediately
    FLAGGED: confidence flag_threshold to auto_threshold -> write with review flag
    HUMAN:   confidence < flag_threshold OR high-risk type -> queue for operator
- Kill switch: pause() / resume() halt all writes immediately
- Integrates with CoPRESTClient for actual HTTP delivery (or dry-run)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate
from chat_to_cop.output.cop_rest_client import CoPRESTClient
from chat_to_cop.output.cop_schema import (
    CoPRecord,
    WriteAuthority,
    cop_update_to_records,
)
from chat_to_cop.output.schema_adapter import PassthroughAdapter, SchemaAdapter


@dataclass
class WriteResult:
    """Result of a single CoP write attempt."""

    record: CoPRecord
    success: bool
    status_code: int | None = None
    error: str | None = None
    dry_run: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CoPWriter:
    """Pushes CoPUpdates to the CoP REST API with tiered write authority.

    Modes:
    - No rest_client: local-only mode (records are mapped but not sent)
    - rest_client provided: delegates HTTP to CoPRESTClient (which may be dry-run)
    - Legacy: cop_base_url + http_client for backward compat with existing tests

    Kill switch:
    - pause(): stops all writes immediately (queues incoming updates)
    - resume(): allows new writes; call flush_pause_queue() to drain backlog

    Configurable thresholds:
    - auto_threshold: minimum confidence for AUTO tier (default 0.95, raised from
      0.7 after ECE=0.67 calibration finding on Qwen2.5-7B — see #58)
    - flag_threshold: minimum confidence for FLAGGED tier (default 0.5, raised from 0.4)
    - high_risk_types: update types that always require HUMAN review
    """

    def __init__(
        self,
        cop_base_url: str | None = None,
        http_client=None,
        rest_client: CoPRESTClient | None = None,
        max_queue_size: int = 1000,
        auto_threshold: float = 0.95,
        flag_threshold: float = 0.5,
        high_risk_types: list[str] | None = None,
        adapter: SchemaAdapter | None = None,
    ) -> None:
        self._base_url = cop_base_url
        self._client = http_client
        self._rest_client = rest_client
        self._paused = False
        self._pause_queue: deque[CoPUpdate] = deque(maxlen=max_queue_size)
        self._human_queue: deque[CoPRecord] = deque(maxlen=max_queue_size)
        self._max_queue_size = max_queue_size
        # Issue #70: the legacy http_client path (cop_base_url + http_client)
        # also needs to run records through the schema adapter, otherwise a
        # test or operator who constructs a CoPWriter that bypasses
        # CoPRESTClient silently sends the pre-#70 wire format. Default
        # Passthrough preserves that pre-#70 behavior.
        self._adapter: SchemaAdapter = adapter if adapter is not None else PassthroughAdapter()

        # Configurable thresholds
        self._auto_threshold = auto_threshold
        self._flag_threshold = flag_threshold
        self._high_risk_types = frozenset(high_risk_types or ["weapons", "csar", "fire_mission", "cyber_ew"])

        # Counters for observability
        self._stats = {
            "auto_writes": 0,
            "flagged_writes": 0,
            "human_queued": 0,
            "errors": 0,
            "paused_queued": 0,
            "total_records": 0,
        }

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def human_queue_size(self) -> int:
        return len(self._human_queue)

    @property
    def pause_queue_size(self) -> int:
        return len(self._pause_queue)

    def pause(self) -> None:
        """Kill switch: immediately stop all writes to the CoP.

        Incoming updates are queued and will be flushed on resume().
        """
        if not self._paused:
            logger.warning("CoPWriter PAUSED - all writes halted")
            self._paused = True
            metrics.inc("cop_writer_paused_total")

    def resume(self) -> None:
        """Resume writes. Does NOT auto-flush -- call flush_pause_queue() to drain."""
        if self._paused:
            logger.info("CoPWriter RESUMED - {} updates queued during pause", len(self._pause_queue))
            self._paused = False
            metrics.inc("cop_writer_resumed_total")

    async def flush_pause_queue(self) -> list[WriteResult]:
        """Drain the pause queue by processing all queued updates.

        Returns results for all flushed updates. Should be called after resume().
        """
        results: list[WriteResult] = []
        while self._pause_queue:
            update = self._pause_queue.popleft()
            batch = await self.push_update(update)
            results.extend(batch)
        return results

    def get_human_queue(self) -> list[CoPRecord]:
        """Return records queued for human review (non-destructive peek)."""
        return list(self._human_queue)

    def pop_human_queue(self, count: int | None = None) -> list[CoPRecord]:
        """Remove and return records from the human review queue."""
        result: list[CoPRecord] = []
        n = count if count is not None else len(self._human_queue)
        for _ in range(min(n, len(self._human_queue))):
            result.append(self._human_queue.popleft())
        return result

    async def push_update(self, update: CoPUpdate) -> list[WriteResult]:
        """Process a CoPUpdate: map to CoP records and write per authority tier.

        Returns a WriteResult for each CoPRecord produced.
        """
        # Kill switch: queue if paused
        if self._paused:
            self._pause_queue.append(update)
            self._stats["paused_queued"] += 1
            metrics.inc("cop_writer_paused_queued_total")
            logger.debug("CoPWriter paused -- queued update (queue size: {})", len(self._pause_queue))
            return []

        # Map CoPUpdate -> CoPRecord(s) using configurable thresholds
        records = cop_update_to_records(
            update,
            auto_threshold=self._auto_threshold,
            flag_threshold=self._flag_threshold,
            high_risk_types=self._high_risk_types,
        )
        results: list[WriteResult] = []

        for record in records:
            self._stats["total_records"] += 1
            result = await self._dispatch_record(record)
            results.append(result)

        return results

    async def _dispatch_record(self, record: CoPRecord) -> WriteResult:
        """Route a single CoPRecord based on its write authority tier."""
        if record.write_authority == WriteAuthority.HUMAN:
            return self._queue_for_human(record)
        elif record.write_authority == WriteAuthority.FLAGGED:
            return await self._write_flagged(record)
        else:
            return await self._write_auto(record)

    def _queue_for_human(self, record: CoPRecord) -> WriteResult:
        """Queue a record for human review instead of writing."""
        self._human_queue.append(record)
        self._stats["human_queued"] += 1
        metrics.inc("cop_writer_human_queued_total")
        logger.debug(
            "Queued for human review: {} (conf={:.2f}, reason={})",
            record.record_type,
            record.extraction_confidence,
            record.review_flag,
        )
        return WriteResult(
            record=record,
            success=True,
            status_code=None,
            error=None,
        )

    async def _write_flagged(self, record: CoPRecord) -> WriteResult:
        """Write a flagged record (written with review metadata)."""
        result = await self._send_to_cop(record)
        if result.success:
            self._stats["flagged_writes"] += 1
            metrics.inc("cop_writer_flagged_writes_total")
        return result

    async def _write_auto(self, record: CoPRecord) -> WriteResult:
        """Write an auto-approved record."""
        result = await self._send_to_cop(record)
        if result.success:
            self._stats["auto_writes"] += 1
            metrics.inc("cop_writer_auto_writes_total")
        return result

    async def _send_to_cop(self, record: CoPRecord) -> WriteResult:
        """Send a CoPRecord to the CoP REST API.

        Dispatch order:
        1. If a CoPRESTClient is configured, use it (preferred path).
        2. If a legacy http_client + base_url is configured, use that.
        3. Otherwise, local-only mode (validate mapping but don't send).
        """
        start = time.perf_counter()
        try:
            # Path 1: CoPRESTClient (new preferred path)
            if self._rest_client is not None:
                send_result = await self._rest_client.send_record(record)
                elapsed = time.perf_counter() - start
                metrics.observe("cop_writer_send_seconds", elapsed)
                if not send_result.success:
                    self._stats["errors"] += 1
                    metrics.inc("cop_writer_errors_total")
                return WriteResult(
                    record=record,
                    success=send_result.success,
                    status_code=send_result.status_code,
                    error=send_result.error,
                    dry_run=send_result.dry_run,
                )

            # Path 2: Legacy http_client
            if self._base_url is not None and self._client is not None:
                endpoint = self._resolve_endpoint(record)
                # Issue #70: route through the adapter so the legacy path
                # emits the same wire shape as CoPRESTClient. Without this,
                # a writer constructed with (cop_base_url, http_client) would
                # silently bypass the schema translation.
                payload = self._adapter.map(record)
                response = await self._client.post(endpoint, json=payload)
                elapsed = time.perf_counter() - start
                metrics.observe("cop_writer_send_seconds", elapsed)
                success = 200 <= response.status_code < 300
                if not success:
                    self._stats["errors"] += 1
                    metrics.inc("cop_writer_errors_total")
                    logger.warning(
                        "CoP write failed: {} {} (status={})",
                        record.record_type,
                        endpoint,
                        response.status_code,
                    )
                return WriteResult(
                    record=record,
                    success=success,
                    status_code=response.status_code,
                )

            # Path 3: Local-only mode
            elapsed = time.perf_counter() - start
            metrics.observe("cop_writer_send_seconds", elapsed)
            logger.trace(
                "Local-only mode: {} record mapped (authority={})",
                record.record_type,
                record.write_authority.value,
            )
            return WriteResult(record=record, success=True, status_code=200)

        except Exception as exc:
            self._stats["errors"] += 1
            metrics.inc("cop_writer_errors_total")
            logger.error("CoP write exception: {}", exc)
            return WriteResult(record=record, success=False, error=str(exc))

    def _resolve_endpoint(self, record: CoPRecord) -> str:
        """Resolve the REST API endpoint for a record type (legacy path)."""
        base = self._base_url or ""
        if record.record_type == "track":
            return f"{base}/api/v1/tracks"
        elif record.record_type == "battle_effect":
            return f"{base}/api/v1/effects"
        else:
            return f"{base}/api/v1/records"

    @classmethod
    def from_config(cls, config) -> CoPWriter:
        """Create a CoPWriter from a CoPWriterConfig."""
        # Issue #70: build the schema adapter from the config field and pass
        # it into the REST client. Default "passthrough" spec preserves
        # prior behavior (record.model_dump). Per the 2026-04-11 !88 lesson,
        # the consumer ships with the Field — not a later MR.
        from chat_to_cop.output.schema_adapter import build_adapter

        adapter = build_adapter(config.schema_adapter)
        rest_client = CoPRESTClient(
            base_url=config.cop_api_url,
            timeout=config.cop_write_timeout,
            retry_attempts=config.cop_retry_attempts,
            adapter=adapter,
        )
        return cls(
            rest_client=rest_client,
            max_queue_size=config.cop_max_queue_size,
            auto_threshold=config.cop_auto_threshold,
            flag_threshold=config.cop_flag_threshold,
            high_risk_types=config.cop_high_risk_types,
            adapter=adapter,
        )
