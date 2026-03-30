"""MLflow experiment tracking integration for the chat-to-cop pipeline.

Optional dependency: if MLflow is not installed, all methods are no-ops.
Controlled by env vars:
    CHAT_TO_COP_TRACKING_ENABLED=true  (default: false)
    CHAT_TO_COP_MLFLOW_URI=http://...  (default: empty, uses MLflow default)
"""

from __future__ import annotations

import os
from collections import defaultdict

from loguru import logger

from chat_to_cop.models.cop_update import CoPUpdate

try:
    import mlflow

    _HAS_MLFLOW = True
except ImportError:
    _HAS_MLFLOW = False


class PipelineTracker:
    """Wraps MLflow experiment tracking with graceful degradation.

    If MLflow is not installed or tracking is disabled, all methods are no-ops.
    """

    def __init__(self) -> None:
        self._enabled = os.environ.get("CHAT_TO_COP_TRACKING_ENABLED", "false").lower() in ("true", "1", "yes")
        self._mlflow_uri = os.environ.get("CHAT_TO_COP_MLFLOW_URI", "")
        self._active = False
        self._type_counts: dict[str, int] = defaultdict(int)
        self._total_extractions = 0

    @property
    def enabled(self) -> bool:
        return self._enabled and _HAS_MLFLOW

    @property
    def active(self) -> bool:
        return self._active

    def start_run(
        self,
        *,
        model: str = "",
        url: str = "",
        timeout: float = 0.0,
        num_ctx: int = 0,
        window_size: int = 0,
        run_name: str = "",
    ) -> None:
        """Start an MLflow run and log pipeline parameters."""
        if not self.enabled:
            return

        try:
            if self._mlflow_uri:
                mlflow.set_tracking_uri(self._mlflow_uri)

            mlflow.set_experiment("chat-to-cop")
            mlflow.start_run(run_name=run_name or None)
            mlflow.log_params(
                {
                    "model": model,
                    "llm_url": url,
                    "timeout": timeout,
                    "num_ctx": num_ctx,
                    "window_size": window_size,
                }
            )
            self._active = True
            logger.info("MLflow run started (experiment: chat-to-cop)")
        except Exception as e:
            logger.warning("Failed to start MLflow run: {}", e)
            self._active = False

    def log_extraction(self, update: CoPUpdate) -> None:
        """Increment per-type extraction counters."""
        if not self._active:
            return

        self._total_extractions += 1
        self._type_counts[update.update_type.value] += 1

    def end_run(self, metrics: dict | None = None) -> None:
        """Log final metrics and end the MLflow run."""
        if not self._active:
            return

        try:
            # Log accumulated type counts
            for utype, count in self._type_counts.items():
                mlflow.log_metric(f"updates_{utype}", count)
            mlflow.log_metric("total_extractions", self._total_extractions)

            # Log any additional summary metrics
            if metrics:
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        mlflow.log_metric(key, value)

            mlflow.end_run()
            self._active = False
            logger.info("MLflow run ended ({} extractions logged)", self._total_extractions)
        except Exception as e:
            logger.warning("Failed to end MLflow run: {}", e)
            self._active = False
