"""CoP REST API writer: pushes updates to the actual CoP database.

Forwards CoPUpdates from the world state store to the Track Manager
and SmartPack Manager REST APIs.

Design:
- Optimistic writes (push immediately, don't wait for validation)
- Idempotent (handle duplicate messages from chat + STT gracefully)
- Schema pending from contractor team -- current implementation writes
  to local store only; CoP forwarding activated when API is available
- Tiered write authority (RAI requirement):
    AUTO:    confidence >= 0.7, low-risk type -> write immediately
    FLAGGED: confidence 0.4-0.7 -> write with review flag
    HUMAN:   confidence < 0.4 OR high-risk type -> queue for operator
- Kill switch: pause() / resume() halt all writes immediately
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate
from chat_to_cop.output.cop_schema import (
    CoPRecord,
    WriteAuthority,
    cop_update_to_records,
)


@dataclass
class WriteResult:
    """Result of a single CoP write attempt."""

    record: CoPRecord
    success: bool
    status_code: int | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CoPWriter:
    """Pushes CoPUpdates to the CoP REST API with tiered write authority.

    Modes:
    - cop_base_url=None: local-only mode (records are mapped but not sent)
    - cop_base_url set: sends HTTP requests to the CoP REST endpoint

    Kill switch:
    - pause(): stops all writes immediately (queues incoming updates)
    - resume(): flushes queued updates and resumes normal operation
    """

    def __init__(
        self,
        cop_base_url: str | None = None,
        http_client=None,
        max_queue_size: int = 1000,
    ) -> None:
        self._base_url = cop_base_url
        self._client = http_client
        self._paused = False
        self._pause_queue: deque[CoPUpdate] = deque(maxlen=max_queue_size)
        self._human_queue: deque[CoPRecord] = deque(maxlen=max_queue_size)
        self._max_queue_size = max_queue_size

        # Counters for observability
        self._stats = {
            "auto_writes": 0,
            "flagged_writes": 0,
            "human_queued": 0,
            "errors": 0,
            "paused_queued": 0,
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
        """Resume writes. Does NOT auto-flush — call flush_pause_queue() to drain."""
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
            logger.debug("CoPWriter paused — queued update (queue size: {})", len(self._pause_queue))
            return []

        # Map CoPUpdate -> CoPRecord(s)
        records = cop_update_to_records(update)
        results: list[WriteResult] = []

        for record in records:
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

        If no base_url is configured, runs in local-only mode (records are
        mapped and validated but not transmitted). If a client is provided,
        POSTs to the appropriate endpoint.
        """
        async with metrics.async_timer("cop_writer_send_seconds"):
            # Local-only mode: validate the record mapping but don't send
            if self._base_url is None and self._client is None:
                logger.trace(
                    "Local-only mode: {} record mapped (authority={})",
                    record.record_type,
                    record.write_authority.value,
                )
                return WriteResult(record=record, success=True, status_code=200)

            # HTTP mode: POST to CoP REST API
            try:
                endpoint = self._resolve_endpoint(record)
                payload = record.model_dump(mode="json")

                if self._client is not None:
                    response = await self._client.post(endpoint, json=payload)
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
                else:
                    # base_url set but no client — should not happen in practice
                    return WriteResult(record=record, success=False, error="No HTTP client configured")

            except Exception as exc:
                self._stats["errors"] += 1
                metrics.inc("cop_writer_errors_total")
                logger.error("CoP write exception: {}", exc)
                return WriteResult(record=record, success=False, error=str(exc))

    def _resolve_endpoint(self, record: CoPRecord) -> str:
        """Resolve the REST API endpoint for a record type."""
        base = self._base_url or ""
        if record.record_type == "track":
            return f"{base}/api/v1/tracks"
        elif record.record_type == "battle_effect":
            return f"{base}/api/v1/effects"
        else:
            return f"{base}/api/v1/records"
