"""Per-session agent hierarchy for multi-agent workflows (Task 75, FR-TRACE-009).

Reconstructs the agent tree of one session from the events' ``agent_id`` and the
OpenTelemetry span linkage (Task 71.1): a call's parent agent is the agent of
the attempt whose ``span_id`` matches this call's ``parent_span_id``. This reuses
the existing trace-context plumbing, so no ``parent_agent_id`` column or
collector change is needed. Each node carries its depth (0 for a root agent) and
the count of attempts it made in the session.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models


@dataclass(frozen=True)
class AgentNode:
    """One agent in a session's hierarchy."""

    agent_id: str
    parent_agent_id: str | None
    depth: int
    attempt_count: int


def _depth(agent_id: str, parents: dict[str, str | None]) -> int:
    """Depth of ``agent_id`` in the parent chain (0 for a root); cycle-safe."""
    depth = 0
    seen: set[str] = set()
    current = parents.get(agent_id)
    while current is not None and current not in seen:
        seen.add(agent_id if depth == 0 else current)
        depth += 1
        agent_id = current
        current = parents.get(current)
    return depth


async def session_agents(
    session: AsyncSession, provider: str, source: str, session_id: str
) -> list[AgentNode]:
    """Return the agent hierarchy of one scoped session, roots first."""
    event = models.UsageEventV2
    src = models.Source
    rows = (
        await session.execute(
            select(event.agent_id, event.span_id, event.parent_span_id)
            .join(src, src.id == event.source_id, isouter=True)
            .where(
                event.event_kind == "attempt",
                event.finality == "final",
                event.provider == provider,
                func.coalesce(event.session_id, "") == session_id,
                func.coalesce(src.name, "") == source,
                event.agent_id.is_not(None),
            )
        )
    ).all()

    span_to_agent: dict[str, str] = {
        span_id: agent_id for agent_id, span_id, _ in rows if span_id
    }
    counts: dict[str, int] = {}
    parents: dict[str, str | None] = {}
    for agent_id, _span_id, parent_span_id in rows:
        counts[agent_id] = counts.get(agent_id, 0) + 1
        parent_agent = (
            span_to_agent.get(parent_span_id) if parent_span_id else None
        )
        if parent_agent is not None and parent_agent != agent_id:
            parents.setdefault(agent_id, parent_agent)
    for agent_id in counts:
        parents.setdefault(agent_id, None)

    nodes = [
        AgentNode(
            agent_id=agent_id,
            parent_agent_id=parents[agent_id],
            depth=_depth(agent_id, parents),
            attempt_count=counts[agent_id],
        )
        for agent_id in counts
    ]
    return sorted(nodes, key=lambda node: (node.depth, node.agent_id))
