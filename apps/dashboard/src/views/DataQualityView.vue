<script setup lang="ts">
// Data-quality feed (FR-UI-008, US-010/012) on /api/v2/data-quality: recorded
// anomalies -- unknown providers/models, unpriced usage, sequence conflicts,
// schema drift, limit-source failures, clock skew -- filterable by kind and
// resolved state, with deep links to where each is fixed (unknown model ->
// pricing admin, unpriced -> costs). Resolve-marking is read-only for now: the
// v2 API exposes no resolve mutation (tracked separately).
import { computed, onMounted, ref } from 'vue';
import AsyncState from '@/components/AsyncState.vue';
import { RouterLink } from 'vue-router';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { formatDateTime, timeAgo } from '@/lib/format';
import { deepLinkFor } from '@/lib/dataQuality';
import type { DataQualityEventV2 } from '@/api/types-v2';

const { loading, error, run, retry } = useAsync();
const events = ref<DataQualityEventV2[]>([]);
const cursor = ref<string | null>(null);
const kindFilter = ref<string>('');
const resolvedFilter = ref<'all' | 'open' | 'resolved'>('open');

// Kinds present in the loaded feed, for the filter dropdown.
const kinds = computed(() =>
  [...new Set(events.value.map((e) => e.kind))].sort()
);

function detailText(detail: Record<string, unknown>): string {
  const entries = Object.entries(detail);
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => `${k}=${String(v)}`).join(', ');
}

function resolvedParam(): boolean | undefined {
  if (resolvedFilter.value === 'open') return false;
  if (resolvedFilter.value === 'resolved') return true;
  return undefined;
}

async function load(append = false): Promise<void> {
  await run(async () => {
    const res = await useClient().v2DataQuality({
      kind: kindFilter.value || undefined,
      resolved: resolvedParam(),
      cursor: append ? (cursor.value ?? undefined) : undefined,
      limit: 100,
    });
    events.value = append ? [...events.value, ...res.events] : res.events;
    cursor.value = res.next_cursor;
  });
}

function reload(): void {
  void load(false);
}

onMounted(() => {
  void load(false);
});
</script>

<template>
  <div>
    <section class="card">
      <div class="head">
        <h3>Data quality</h3>
        <div class="filters">
          <select v-model="kindFilter" @change="reload">
            <option value="">all kinds</option>
            <option v-for="k in kinds" :key="k" :value="k">{{ k }}</option>
          </select>
          <select v-model="resolvedFilter" @change="reload">
            <option value="open">open</option>
            <option value="resolved">resolved</option>
            <option value="all">all</option>
          </select>
        </div>
      </div>
      <p class="muted small note">
        Recorded anomalies from ingest — unknown providers/models, unpriced
        usage, drift, and more. Each links to where it's fixed.
      </p>

      <AsyncState
        :loading="loading && events.length === 0"
        :error="error"
        :empty="!loading && events.length === 0"
        empty-text="No data-quality events — everything looks clean."
        @retry="retry"
      >
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Subject</th>
              <th>Detail</th>
              <th>State</th>
              <th>When</th>
              <th>Fix</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in events" :key="e.id">
              <td>
                <span class="badge">{{ e.kind }}</span>
              </td>
              <td class="mono">{{ e.subject }}</td>
              <td class="detail muted">{{ detailText(e.detail) }}</td>
              <td>
                <span :class="e.resolved ? 'resolved' : 'open'">
                  {{ e.resolved ? 'resolved' : 'open' }}
                </span>
              </td>
              <td :title="formatDateTime(e.ts)">{{ timeAgo(e.ts) }}</td>
              <td>
                <RouterLink
                  v-if="deepLinkFor(e.kind)"
                  :to="deepLinkFor(e.kind)!.to"
                  class="link"
                >
                  {{ deepLinkFor(e.kind)!.label }} →
                </RouterLink>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <button v-if="cursor" class="more" @click="load(true)">
          Load more
        </button>
      </AsyncState>
    </section>
  </div>
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
}
.filters {
  display: flex;
  gap: 0.5rem;
}
select {
  font: inherit;
  padding: 0.35rem 0.5rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
.small {
  font-size: 0.78rem;
}
.note {
  margin: 0.5rem 0 1rem;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.badge {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.detail {
  max-width: 30ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.open {
  color: var(--status-warning);
}
.resolved {
  color: var(--status-good);
}
.link {
  color: var(--series-1);
}
.more {
  margin-top: 0.75rem;
  font: inherit;
  padding: 0.4rem 0.9rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  cursor: pointer;
}
</style>
