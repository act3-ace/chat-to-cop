"""Supervisor: agent lifecycle and health management.

Lightweight Python process (NOT an LLM) that manages the agent pool.

Responsibilities:
- Health monitoring (latency, error rate, backend status per agent)
- Agent lifecycle (start, stop, restart, reassign channels)
- Priority management (shed load by deprioritizing LOW channels)
- New channel detection (spawn agent for channels appearing mid-exercise)
- Metrics exposure (FastAPI /health and /agents endpoints)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI
from loguru import logger

from chat_to_cop.agent.channel_agent import ChannelAgent
from chat_to_cop.backend.base import LLMBackend
from chat_to_cop.metrics import metrics
from chat_to_cop.models.cop_update import CoPUpdate, UpdateType
from chat_to_cop.models.feedback import FusionFeedback
from chat_to_cop.models.messages import CHANNEL_PRIORITIES, ChannelPriority, IRCMessage


@dataclass
class AgentHealth:
    """Health snapshot for a single channel agent."""

    channel: str
    priority: ChannelPriority
    is_active: bool = True
    messages_processed: int = 0
    errors: int = 0
    last_error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Supervisor:
    """Lightweight orchestrator managing the channel agent pool.

    NOT an LLM — pure Python orchestration. Manages agent lifecycle,
    monitors health via the metrics system, and sheds load by channel
    priority when the system is overloaded.
    """

    def __init__(
        self,
        backend_factory: Callable[[], LLMBackend],
        *,
        latency_threshold: float = 5.0,
        error_rate_threshold: float = 0.1,
        health_check_interval: float = 5.0,
        max_restart_attempts: int = 3,
    ) -> None:
        self._backend_factory = backend_factory
        self._agents: dict[str, ChannelAgent] = {}
        self._health: dict[str, AgentHealth] = {}
        self._shed_channels: set[str] = set()
        self._latency_threshold = latency_threshold
        self._error_rate_threshold = error_rate_threshold
        self._health_check_interval = health_check_interval
        self._max_restart_attempts = max_restart_attempts
        self._restart_counts: dict[str, int] = {}
        self._running = False

    # -- Properties --

    @property
    def agents(self) -> dict[str, ChannelAgent]:
        return dict(self._agents)

    @property
    def active_channels(self) -> list[str]:
        return [ch for ch in self._agents if ch not in self._shed_channels]

    @property
    def shed_channels(self) -> set[str]:
        return set(self._shed_channels)

    # -- Agent lifecycle --

    def start_agent(self, channel: str, priority: ChannelPriority | None = None) -> ChannelAgent:
        """Start a new agent for a channel. Replaces existing if present."""
        if channel in self._agents:
            logger.warning(f"Agent for {channel} already exists, replacing")
            self.stop_agent(channel)

        backend = self._backend_factory()
        agent = ChannelAgent(channel=channel, backend=backend)
        self._agents[channel] = agent

        if priority is None:
            priority = CHANNEL_PRIORITIES.get(channel, ChannelPriority.MEDIUM)

        self._health[channel] = AgentHealth(channel=channel, priority=priority)
        self._restart_counts.setdefault(channel, 0)

        logger.info(f"Started agent for {channel} (priority={priority.value})")
        metrics.inc("supervisor_agents_started_total", labels={"channel": channel})
        return agent

    def stop_agent(self, channel: str) -> None:
        """Stop and remove an agent."""
        if channel not in self._agents:
            logger.warning(f"No agent for {channel} to stop")
            return

        del self._agents[channel]
        self._shed_channels.discard(channel)
        logger.info(f"Stopped agent for {channel}")
        metrics.inc("supervisor_agents_stopped_total", labels={"channel": channel})

    def restart_agent(self, channel: str) -> ChannelAgent:
        """Restart an agent, preserving its priority."""
        priority = self._health[channel].priority if channel in self._health else None
        self._restart_counts[channel] = self._restart_counts.get(channel, 0) + 1
        metrics.inc("supervisor_agents_restarted_total", labels={"channel": channel})
        logger.warning(f"Restarting agent for {channel} (attempt {self._restart_counts[channel]})")

        self.stop_agent(channel)
        return self.start_agent(channel, priority=priority)

    # -- Message routing --

    async def route_message(self, message: IRCMessage) -> list[CoPUpdate]:
        """Route a message to the appropriate channel agent.

        Spawns a new agent if the channel hasn't been seen (dynamic assignment).
        Returns a shed passthrough if the channel is currently load-shed.
        """
        channel = message.channel

        # Dynamic channel assignment
        if channel not in self._agents:
            self.start_agent(channel)

        # Load shedding: return passthrough for shed channels
        if channel in self._shed_channels:
            metrics.inc("supervisor_messages_shed_total", labels={"channel": channel})
            return [
                CoPUpdate(
                    update_type=UpdateType.NONE,
                    confidence=0.0,
                    extraction_method="shed",
                    entities=[],
                    source_channel=channel,
                    source_speaker=message.sender,
                    source_message=message.content,
                    timestamp=message.timestamp,
                    reasoning=f"Channel {channel} shed due to system load",
                )
            ]

        # Delegate to channel agent
        health = self._health[channel]
        try:
            updates = await self._agents[channel].process_message(message)
            health.messages_processed = self._agents[channel].message_count
            return updates
        except Exception as e:
            # Belt-and-suspenders: ChannelAgent already catches internally,
            # but guard against unexpected failures.
            health.errors += 1
            health.last_error = str(e)
            metrics.inc("supervisor_routing_errors_total", labels={"channel": channel})
            logger.error(f"Unhandled error routing to {channel}: {e}")
            return [
                CoPUpdate(
                    update_type=UpdateType.NONE,
                    confidence=0.0,
                    extraction_method="error",
                    entities=[],
                    source_channel=channel,
                    source_speaker=message.sender,
                    source_message=message.content,
                    timestamp=message.timestamp,
                    reasoning=f"Supervisor routing error: {e}",
                )
            ]

    # -- Feedback routing --

    def route_feedback(self, feedback_list: list[FusionFeedback]) -> None:
        """Route fusion feedback to the appropriate channel agents.

        Each feedback item targets a specific channel+speaker. The supervisor
        forwards to the correct agent, silently skipping if the agent doesn't exist.
        """
        for feedback in feedback_list:
            agent = self._agents.get(feedback.source_channel)
            if agent is not None:
                agent.receive_fusion_feedback(feedback)

    # -- Health monitoring --

    def _get_agent_error_rate(self, channel: str) -> float:
        labels = {"channel": channel}
        received = metrics.get_counter("agent_messages_received_total", labels=labels)
        errors = metrics.get_counter("agent_extraction_errors_total", labels=labels)
        if received == 0:
            return 0.0
        return errors / received

    def _get_agent_latency(self, channel: str) -> float:
        labels = {"channel": channel}
        hist = metrics.get_histogram("agent_extraction_latency_seconds", labels=labels)
        return hist["max"]  # Max as proxy for worst-case latency

    def _is_system_overloaded(self) -> bool:
        """True when majority of active agents exceed latency or error thresholds."""
        active = self.active_channels
        if not active:
            return False

        overloaded = 0
        for channel in active:
            latency = self._get_agent_latency(channel)
            error_rate = self._get_agent_error_rate(channel)
            if latency > self._latency_threshold or error_rate > self._error_rate_threshold:
                overloaded += 1

        return overloaded > len(active) / 2

    def _evaluate_load_shedding(self) -> None:
        """Shed or restore channels based on system health."""
        if self._is_system_overloaded():
            # Shed LOW first, then MEDIUM. Never shed HIGH.
            for priority in [ChannelPriority.LOW, ChannelPriority.MEDIUM]:
                for channel, health in self._health.items():
                    if health.priority == priority and channel not in self._shed_channels and channel in self._agents:
                        self._shed_channels.add(channel)
                        health.is_active = False
                        logger.warning(f"Shedding {channel} (priority={priority.value})")
                        metrics.inc("supervisor_channels_shed_total", labels={"channel": channel})

                # Re-check after shedding this tier
                if not self._is_system_overloaded():
                    break
        elif self._shed_channels:
            # System recovered — restore one priority tier per cycle
            for priority in [ChannelPriority.HIGH, ChannelPriority.MEDIUM, ChannelPriority.LOW]:
                restored = [
                    ch for ch in self._shed_channels if self._health.get(ch) and self._health[ch].priority == priority
                ]
                if restored:
                    for ch in restored:
                        self._shed_channels.discard(ch)
                        self._health[ch].is_active = True
                        metrics.inc("supervisor_channels_restored_total", labels={"channel": ch})
                    logger.info(f"Restored channels: {', '.join(restored)}")
                    break  # One tier per cycle

    def _evaluate_restarts(self) -> None:
        """Restart agents with high error rates."""
        for channel in list(self._agents.keys()):
            error_rate = self._get_agent_error_rate(channel)
            if error_rate > self._error_rate_threshold:
                attempts = self._restart_counts.get(channel, 0)
                if attempts < self._max_restart_attempts:
                    logger.warning(f"Agent {channel} error rate {error_rate:.2%}, restarting")
                    self.restart_agent(channel)

    def health_check(self) -> None:
        """Run a health check: sync stats, evaluate restarts and load shedding."""
        # Sync health from metrics
        for channel in list(self._agents.keys()):
            health = self._health.get(channel)
            if not health:
                continue
            health.messages_processed = self._agents[channel].message_count
            health.errors = metrics.get_counter("agent_extraction_errors_total", labels={"channel": channel})

        self._evaluate_restarts()
        self._evaluate_load_shedding()
        metrics.inc("supervisor_health_checks_total")

    # -- Main loop --

    async def run(self) -> None:
        """Main supervisor loop: periodic health checks."""
        self._running = True
        logger.info(f"Supervisor started (health_check_interval={self._health_check_interval}s)")

        while self._running:
            try:
                self.health_check()
            except Exception as e:
                logger.error(f"Health check failed: {e}")
            await asyncio.sleep(self._health_check_interval)

    def stop(self) -> None:
        """Signal the supervisor to stop its run loop."""
        self._running = False

    # -- Status (for FastAPI) --

    def agent_status(self) -> list[dict]:
        """Per-agent health status for the /agents endpoint."""
        result = []
        for channel, health in self._health.items():
            latency_hist = metrics.get_histogram("agent_extraction_latency_seconds", labels={"channel": channel})
            result.append(
                {
                    "channel": channel,
                    "priority": health.priority.value,
                    "is_active": health.is_active,
                    "messages_processed": health.messages_processed,
                    "errors": health.errors,
                    "error_rate": round(self._get_agent_error_rate(channel), 4),
                    "mean_latency": round(latency_hist["mean"], 4),
                    "max_latency": round(latency_hist["max"], 4),
                    "last_error": health.last_error,
                    "started_at": health.started_at.isoformat(),
                }
            )
        return result

    def system_health(self) -> dict:
        """Overall system health for the /health endpoint."""
        total = len(self._agents)
        active = len(self.active_channels)
        return {
            "status": "degraded" if self._shed_channels else "healthy",
            "total_agents": total,
            "active_agents": active,
            "shed_channels": sorted(self._shed_channels),
            "overloaded": self._is_system_overloaded(),
        }


def create_health_app(supervisor: Supervisor) -> FastAPI:
    """Create a FastAPI app exposing supervisor health endpoints."""
    app = FastAPI(title="chat-to-cop supervisor", version="0.1.0")

    @app.get("/health")
    def health():
        return supervisor.system_health()

    @app.get("/agents")
    def agents():
        return supervisor.agent_status()

    return app
