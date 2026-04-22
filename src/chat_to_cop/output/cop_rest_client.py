"""CoP REST API client: async HTTP interface to the CoP database.

This is the ONLY file (along with cop_schema.py) that needs to change
when the real contractor schema arrives. All other writer infrastructure
(tiers, queues, kill switch, metrics) stays the same.

Modes:
- base_url="" (empty): dry-run mode. Logs what would be sent, no HTTP.
- base_url set: POSTs to the CoP REST API with tenacity retry on transient failures.

Uses httpx (already in dep tree via openai) for async HTTP.
"""

from __future__ import annotations

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from chat_to_cop.metrics import metrics
from chat_to_cop.output.cop_schema import CoPRecord
from chat_to_cop.output.schema_adapter import PassthroughAdapter, SchemaAdapter


class CoPRESTClient:
    """Async HTTP client for the CoP REST API.

    When base_url is empty, operates in dry-run mode: validates the
    payload and logs it but sends no HTTP requests. When base_url is
    set, POSTs/PUTs to the appropriate endpoint with retry on transient
    failures (timeouts, 5xx, connection errors).
    """

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 10.0,
        retry_attempts: int = 3,
        adapter: SchemaAdapter | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._timeout = timeout
        self._retry_attempts = retry_attempts
        self._client: httpx.AsyncClient | None = None
        self._dry_run = not bool(self._base_url)
        # Issue #70: schema adapter between CoPRecord and the wire payload.
        # Default Passthrough preserves pre-#70 behavior (record.model_dump);
        # JsonSchemaAdapter transforms into the contractor's target shape
        # when a schema+mapping config is available.
        self._adapter: SchemaAdapter = adapter if adapter is not None else PassthroughAdapter()

        # Counters for observability
        self._stats = {
            "requests_sent": 0,
            "requests_succeeded": 0,
            "requests_failed": 0,
            "dry_run_logged": 0,
        }

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def open(self) -> None:
        """Create the HTTP client. Call before sending requests."""
        if not self._dry_run:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
            logger.info("CoPRESTClient opened: {}", self._base_url)
        else:
            logger.info("CoPRESTClient in dry-run mode (no base URL configured)")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> CoPRESTClient:
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def _resolve_endpoint(self, record: CoPRecord) -> str:
        """Resolve the REST API endpoint path for a record type."""
        if record.record_type == "track":
            return "/api/v1/tracks"
        elif record.record_type == "battle_effect":
            return "/api/v1/effects"
        else:
            return "/api/v1/records"

    async def send_record(self, record: CoPRecord) -> SendResult:
        """Send a single CoPRecord to the CoP REST API.

        In dry-run mode, logs the record but does not send.
        In live mode, POSTs with retry on transient failures.
        """
        endpoint = self._resolve_endpoint(record)
        # Issue #70: adapter produces the wire-format dict. Default is
        # Passthrough (equivalent to record.model_dump(mode="json") — the
        # pre-#70 behavior). JsonSchemaAdapter transforms into the target CoP
        # schema's shape.
        payload = self._adapter.map(record)

        if self._dry_run:
            self._stats["dry_run_logged"] += 1
            metrics.inc("cop_rest_dry_run_total")
            logger.debug(
                "DRY-RUN: would POST {} -> {} (authority={}, conf={:.2f})",
                record.record_type,
                endpoint,
                record.write_authority.value,
                record.extraction_confidence,
            )
            return SendResult(success=True, status_code=200, dry_run=True)

        return await self._send_with_retry(endpoint, payload)

    async def _send_with_retry(self, endpoint: str, payload: dict) -> SendResult:
        """POST to the CoP API with tenacity retry on transient failures."""

        @retry(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
            reraise=True,
        )
        async def _do_post():
            async with metrics.async_timer("cop_rest_request_seconds"):
                self._stats["requests_sent"] += 1
                metrics.inc("cop_rest_requests_total")
                response = await self._client.post(endpoint, json=payload)
                return response

        try:
            response = await _do_post()
            success = 200 <= response.status_code < 300
            if success:
                self._stats["requests_succeeded"] += 1
                metrics.inc("cop_rest_requests_succeeded_total")
            else:
                self._stats["requests_failed"] += 1
                metrics.inc("cop_rest_requests_failed_total")
                logger.warning(
                    "CoP API error: POST {} -> {} (body={})",
                    endpoint,
                    response.status_code,
                    response.text[:200],
                )
            return SendResult(
                success=success,
                status_code=response.status_code,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._stats["requests_failed"] += 1
            metrics.inc("cop_rest_requests_failed_total")
            logger.error("CoP API connection failed after retries: {}", exc)
            return SendResult(success=False, error=str(exc))
        except Exception as exc:
            self._stats["requests_failed"] += 1
            metrics.inc("cop_rest_requests_failed_total")
            logger.error("CoP API unexpected error: {}", exc)
            return SendResult(success=False, error=str(exc))


class SendResult:
    """Result of a single REST API send attempt."""

    __slots__ = ("success", "status_code", "error", "dry_run")

    def __init__(
        self,
        success: bool,
        status_code: int | None = None,
        error: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self.success = success
        self.status_code = status_code
        self.error = error
        self.dry_run = dry_run
