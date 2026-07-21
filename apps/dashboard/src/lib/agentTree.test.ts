import { describe, expect, it } from 'vitest';
import type { AgentNode } from '@/api/client';
import { agentTreeRows, totalAgentAttempts } from './agentTree';

const nodes: AgentNode[] = [
  { agent_id: 'root', parent_agent_id: null, depth: 0, attempt_count: 1 },
  { agent_id: 'child', parent_agent_id: 'root', depth: 1, attempt_count: 2 },
  {
    agent_id: 'grandchild',
    parent_agent_id: 'child',
    depth: 2,
    attempt_count: 1,
  },
  { agent_id: 'sibling', parent_agent_id: 'root', depth: 1, attempt_count: 1 },
];

describe('agentTreeRows', () => {
  it('orders parent-before-children (DFS) for indentation', () => {
    const rows = agentTreeRows(nodes).map((r) => r.agentId);
    // root -> child -> grandchild -> sibling
    expect(rows).toEqual(['root', 'child', 'grandchild', 'sibling']);
  });

  it('keeps depth for each row', () => {
    const byId = Object.fromEntries(
      agentTreeRows(nodes).map((r) => [r.agentId, r.depth])
    );
    expect(byId).toEqual({ root: 0, child: 1, grandchild: 2, sibling: 1 });
  });

  it('is empty for no agents', () => {
    expect(agentTreeRows([])).toEqual([]);
  });
});

describe('totalAgentAttempts', () => {
  it('sums attempt counts', () => {
    expect(totalAgentAttempts(nodes)).toBe(5);
  });
});
