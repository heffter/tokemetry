// Agent-hierarchy display helpers, extracted from SessionsView so the tree
// ordering is unit-testable. Mirrors GET /api/v2/sessions/{id}/agents (Task 75).
import type { AgentNode } from '@/api/client';

export interface AgentTreeRow {
  agentId: string;
  depth: number;
  attemptCount: number;
}

// Order the flat agent list into a parent-before-children (DFS) sequence for an
// indented tree, so a child always renders directly under its parent.
export function agentTreeRows(nodes: AgentNode[]): AgentTreeRow[] {
  const byParent = new Map<string | null, AgentNode[]>();
  for (const node of nodes) {
    const key = node.parent_agent_id;
    const siblings = byParent.get(key);
    if (siblings) siblings.push(node);
    else byParent.set(key, [node]);
  }
  const rows: AgentTreeRow[] = [];
  const seen = new Set<string>();
  const visit = (parent: string | null): void => {
    for (const node of byParent.get(parent) ?? []) {
      if (seen.has(node.agent_id)) continue; // cycle guard
      seen.add(node.agent_id);
      rows.push({
        agentId: node.agent_id,
        depth: node.depth,
        attemptCount: node.attempt_count,
      });
      visit(node.agent_id);
    }
  };
  visit(null);
  return rows;
}

export function totalAgentAttempts(nodes: AgentNode[]): number {
  return nodes.reduce((sum, node) => sum + node.attempt_count, 0);
}
