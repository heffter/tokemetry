<script setup lang="ts">
// Sessions, keyed by scoped identity (provider + source + session_id) so
// sessions from different sources never collide even when they share a
// session_id (FR-TRACE-011); Claude Code sessions still show their familiar
// session_id (FR-TRACE-010). The list comes from /api/v2/sessions; expanding a
// row fetches that session's attempts and derives provider-neutral stats --
// attempts, fallbacks, success rate, cache ratio, latency (FR-TRACE-012).
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { useGlobalFilters } from '@/composables/useGlobalFilters';
import { barOption } from '@/lib/charts';
import {
  formatCost,
  formatDateTime,
  formatPct,
  formatTokens,
  timeAgo,
} from '@/lib/format';
import { attemptSummary, latencyValues, percentile } from '@/lib/trace';
import { agentTreeRows, type AgentTreeRow } from '@/lib/agentTree';
import {
  clampRangeDays,
  dayEndIso,
  dayStartIso,
  presetRange,
} from '@/lib/filters';
import type { AttemptSummary } from '@/lib/trace';
import type { AttemptV2, ProviderV2, SessionV2 } from '@/api/types-v2';

// The v2 sessions/attempts endpoints bound the range at 366 days; keep a margin
// under it so the "recent sessions" window is always accepted.
const MAX_RANGE_DAYS = 365;

/** The most recent MAX_RANGE_DAYS as an ISO-datetime {from, to}. */
function boundedRange(): { from: string; to: string } {
  const all = presetRange('all');
  const clamped = clampRangeDays(all.from, all.to, MAX_RANGE_DAYS);
  return {
    from: dayStartIso(clamped.from),
    // Inclusive end-of-day so sessions active today are in the recent window.
    to: dayEndIso(clamped.to),
  };
}

const { loading, error, run, retry } = useAsync();
const { provider: globalProvider } = useGlobalFilters();
const sessions = ref<SessionV2[]>([]);
const providers = ref<ProviderV2[]>([]);
const projects = ref<string[]>([]);
const providerFilter = ref<string>('');
// Project filter is applied server-side (a session touching the project, not
// only ones whose dominant project matches), so it reloads the list.
const projectFilter = ref<string>('');

/** Last path segment of a project path, e.g. "…/worktrees/foo" -> "foo". */
function projectLabel(path: string | null): string {
  if (!path) return '—';
  const segments = path.split(/[\\/]/).filter(Boolean);
  return segments.length ? segments[segments.length - 1] : path;
}

const providerName = computed(() => {
  const map = new Map(providers.value.map((p) => [p.id, p.display_name]));
  return (id: string): string => map.get(id) ?? id;
});
const providerList = computed(() =>
  [...new Set(sessions.value.map((s) => s.provider))].sort()
);

// Both the global provider filter and the local select narrow the list.
const filtered = computed(() =>
  sessions.value.filter(
    (s) =>
      (!globalProvider.value || s.provider === globalProvider.value) &&
      (!providerFilter.value || s.provider === providerFilter.value)
  )
);

type SortKey = 'total_tokens' | 'cost_usd' | 'attempt_count' | 'ts_last';
const sortKey = ref<SortKey>('total_tokens');
const sortDir = ref<'asc' | 'desc'>('desc');

const sorted = computed(() => {
  const dir = sortDir.value === 'asc' ? 1 : -1;
  const key = sortKey.value;
  return [...filtered.value].sort((a, b) => {
    if (key === 'ts_last') {
      const ta = a.ts_last ? new Date(a.ts_last).getTime() : 0;
      const tb = b.ts_last ? new Date(b.ts_last).getTime() : 0;
      return (ta - tb) * dir;
    }
    if (key === 'cost_usd') {
      return (Number(a.cost_usd ?? 0) - Number(b.cost_usd ?? 0)) * dir;
    }
    return (a[key] - b[key]) * dir;
  });
});

const totals = computed(() => ({
  count: filtered.value.length,
  tokens: filtered.value.reduce((sum, s) => sum + s.total_tokens, 0),
  attempts: filtered.value.reduce((sum, s) => sum + s.attempt_count, 0),
  cost: filtered.value.reduce((sum, s) => sum + Number(s.cost_usd ?? 0), 0),
}));

const topChart = computed(() => {
  const top = [...filtered.value]
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .slice(0, 10);
  return barOption(
    top.map((s) => s.session_id.slice(0, 8)),
    top.map((s) => s.total_tokens),
    'tokens'
  );
});

function sortBy(key: SortKey): void {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
  } else {
    sortKey.value = key;
    sortDir.value = 'desc';
  }
}
function arrow(key: SortKey): string {
  if (sortKey.value !== key) return '';
  return sortDir.value === 'desc' ? ' ▾' : ' ▴';
}

// Drilldown keyed by scoped_id so two same-session_id rows expand independently.
const expandedId = ref<string | null>(null);
const detail = ref<{
  summary: AttemptSummary;
  p50: number | null;
  p95: number | null;
  agents: AgentTreeRow[];
} | null>(null);
const detailLoading = ref(false);

async function toggleDetail(session: SessionV2): Promise<void> {
  if (expandedId.value === session.scoped_id) {
    expandedId.value = null;
    detail.value = null;
    return;
  }
  expandedId.value = session.scoped_id;
  detail.value = null;
  detailLoading.value = true;
  try {
    const range = boundedRange();
    const attempts: AttemptV2[] = (
      await useClient().v2Attempts({
        from: session.ts_first ?? range.from,
        to: session.ts_last ?? range.to,
        provider: session.provider,
        session: session.session_id,
        limit: 200,
      })
    ).attempts;
    const values = latencyValues(attempts);
    // The agent hierarchy is best-effort so an older server (without the
    // endpoint) still shows the rest of the drilldown (Task 75).
    let agents: AgentTreeRow[] = [];
    try {
      agents = agentTreeRows(
        (await useClient().sessionAgents(session.scoped_id)).agents
      );
    } catch {
      agents = [];
    }
    detail.value = {
      summary: attemptSummary(attempts),
      p50: percentile(values, 50),
      p95: percentile(values, 95),
      agents,
    };
  } finally {
    detailLoading.value = false;
  }
}

function latencyText(ms: number | null): string {
  if (ms === null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const { from, to } = boundedRange();
    const project = projectFilter.value || undefined;
    const rows: SessionV2[] = [];
    let cursor: string | undefined;
    // Page through (keyset) up to a guard cap so the sortable table has the set.
    for (let page = 0; page < 20; page += 1) {
      const res = await client.v2Sessions({
        from,
        to,
        project,
        limit: 200,
        cursor,
      });
      rows.push(...res.sessions);
      if (!res.next_cursor) break;
      cursor = res.next_cursor;
    }
    sessions.value = rows;
  });
}

async function loadOptions(): Promise<void> {
  try {
    providers.value = await useClient().v2Providers();
  } catch {
    providers.value = [];
  }
  // Project list for the filter; non-critical, so a failure just leaves it empty.
  try {
    const all = presetRange('all');
    projects.value = (
      await useClient().usage({ groupBy: 'project', ...all })
    ).buckets
      .filter((b) => b.key)
      .sort((a, b) => b.total_tokens - a.total_tokens)
      .map((b) => b.key);
  } catch {
    projects.value = [];
  }
}

function onProjectFilter(): void {
  void load();
}

onMounted(() => {
  void loadOptions();
  void load();
});
</script>

<template>
  <AsyncState
    :loading="loading && sessions.length === 0"
    :error="error"
    :empty="!loading && sessions.length === 0"
    empty-text="No sessions yet."
    @retry="retry"
  >
    <section class="card">
      <div class="head">
        <h3>Sessions</h3>
        <div class="filters">
          <select v-model="providerFilter">
            <option value="">all providers</option>
            <option v-for="p in providerList" :key="p" :value="p">
              {{ providerName(p) }}
            </option>
          </select>
          <select
            v-model="projectFilter"
            title="Sessions that touched this project"
            @change="onProjectFilter"
          >
            <option value="">all projects</option>
            <option v-for="p in projects" :key="p" :value="p">
              {{ projectLabel(p) }}
            </option>
          </select>
        </div>
      </div>

      <p class="muted small note">
        Sessions from the last {{ MAX_RANGE_DAYS }} days, keyed by provider +
        source + session id.
      </p>

      <EChart :option="topChart" height="220px" />

      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Provider</th>
              <th>Source</th>
              <th>Project</th>
              <th class="num sortable" @click="sortBy('attempt_count')">
                Attempts{{ arrow('attempt_count') }}
              </th>
              <th class="num sortable" @click="sortBy('total_tokens')">
                Tokens{{ arrow('total_tokens') }}
              </th>
              <th class="num sortable" @click="sortBy('cost_usd')">
                Cost{{ arrow('cost_usd') }}
              </th>
              <th class="sortable" @click="sortBy('ts_last')">
                Last active{{ arrow('ts_last') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <template v-for="s in sorted" :key="s.scoped_id">
              <tr class="clickable" @click="toggleDetail(s)">
                <td class="mono" :title="s.session_id || '(no session id)'">
                  {{ expandedId === s.scoped_id ? '▾' : '▸' }}
                  {{ s.session_id ? s.session_id.slice(0, 8) : '(no id)' }}
                </td>
                <td>{{ providerName(s.provider) }}</td>
                <td class="muted">{{ s.source }}</td>
                <td :title="s.primary_project || 'unattributed'">
                  {{ projectLabel(s.primary_project) }}
                </td>
                <td class="num tabular">{{ s.attempt_count }}</td>
                <td class="num tabular">{{ formatTokens(s.total_tokens) }}</td>
                <td class="num tabular">
                  <span
                    v-if="s.cost_usd === null"
                    class="unpriced"
                    title="No price for this model"
                    >unpriced</span
                  >
                  <template v-else>{{ formatCost(s.cost_usd) }}</template>
                </td>
                <td :title="s.ts_last ? formatDateTime(s.ts_last) : ''">
                  {{ s.ts_last ? timeAgo(s.ts_last) : '—' }}
                </td>
              </tr>
              <tr v-if="expandedId === s.scoped_id" class="detail-row">
                <td :colspan="8">
                  <div v-if="detailLoading" class="muted">loading…</div>
                  <div v-else-if="detail" class="chips">
                    <span class="chip"
                      >{{ detail.summary.attempts }} attempts</span
                    >
                    <span
                      class="chip"
                      :class="{ warn: detail.summary.fallbacks > 0 }"
                    >
                      {{ detail.summary.fallbacks }} fallback(s)
                    </span>
                    <span class="chip">
                      {{
                        formatPct(
                          detail.summary.attempts === 0
                            ? 0
                            : (detail.summary.successes /
                                detail.summary.attempts) *
                                100
                        )
                      }}
                      success
                    </span>
                    <span class="chip">
                      {{ formatPct(detail.summary.cacheRatio * 100) }} cache
                    </span>
                    <span class="chip">p50 {{ latencyText(detail.p50) }}</span>
                    <span class="chip">p95 {{ latencyText(detail.p95) }}</span>
                    <span class="chip">
                      {{ detail.summary.logicalRequests }} logical request(s)
                    </span>
                  </div>
                  <ul v-if="detail && detail.agents.length" class="agent-tree">
                    <li
                      v-for="agent in detail.agents"
                      :key="agent.agentId"
                      :style="{ paddingLeft: `${agent.depth * 16}px` }"
                    >
                      <span aria-hidden="true">{{
                        agent.depth > 0 ? '└ ' : ''
                      }}</span>
                      {{ agent.agentId }}
                      <span class="muted small"
                        >({{ agent.attemptCount }})</span
                      >
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
          </tbody>
          <tfoot>
            <tr>
              <td :colspan="4">{{ totals.count }} sessions</td>
              <td class="num tabular">{{ totals.attempts }}</td>
              <td class="num tabular">{{ formatTokens(totals.tokens) }}</td>
              <td class="num tabular">{{ formatCost(String(totals.cost)) }}</td>
              <td></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  </AsyncState>
</template>

<style scoped>
h3 {
  margin: 0;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}
.filters {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
select {
  font: inherit;
  padding: 0.35rem 0.5rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  font-size: 0.9rem;
  margin-top: 1rem;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}
.num {
  text-align: right;
}
.sortable {
  cursor: pointer;
  user-select: none;
}
.sortable:hover {
  color: var(--text-primary);
}
.clickable {
  cursor: pointer;
}
.clickable:hover td {
  background: color-mix(in srgb, var(--gridline) 40%, transparent);
}
.detail-row td {
  background: var(--page);
}
.chips {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
  padding: 0.25rem 0;
}
.chip {
  font-size: 0.8rem;
  padding: 0.15rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
}
.chip.warn {
  color: var(--status-warning);
  border-color: var(--status-warning);
}
tfoot td {
  font-weight: 600;
  border-bottom: none;
}
.mono {
  font-family: ui-monospace, monospace;
}
.small {
  font-size: 0.78rem;
}
.note {
  margin: 0.5rem 0 0;
}
.unpriced {
  font-size: 0.75rem;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.05rem 0.35rem;
}
</style>
