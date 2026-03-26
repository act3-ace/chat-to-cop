"""CoP REST API writer: pushes updates to the actual CoP database.

Forwards CoPUpdates from the world state store to the Track Manager
and SmartPack Manager REST APIs.

Design:
- Optimistic writes (push immediately, don't wait for validation)
- Idempotent (handle duplicate messages from chat + STT gracefully)
- Schema pending from contractor team — current implementation writes
  to local store only; CoP forwarding activated when API is available
"""

from __future__ import annotations


# TODO: Implement CoPWriter
# - __init__(cop_base_url=None)  # None = local-only mode
# - async push_update(update: CoPUpdate)
# - Map CoPUpdate fields to CoP database schema (pending)
# - Queue + retry for transient failures
# - Metrics: updates pushed, latency, errors
