<script setup lang="ts">
// Reporting sources and their health (FR-SOURCE-006, FR-UI-009), backed by
// /api/v2/sources. Each source shows type, linked machine, billing mode,
// freshness, last successful ingest, error count, reported schema version, and
// clock skew; stale sources, schema-version drift, and large clock skew are
// flagged. A source's label and billing mode are editable through the PATCH
// endpoint with a confirmation and optimistic update that rolls back on error.
// The machine fleet view is unchanged (its own /machines route).
import { computed, onMounted, ref } from 'vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { formatDateTime, timeAgo } from '@/lib/format';
import { sourceFlags, supportedSchemaVersion } from '@/lib/sources';
import type { SourceV2 } from '@/api/types-v2';

const BILLING_MODES = ['subscription', 'api_billed', 'unknown'];

const { loading, error, run, retry } = useAsync();
const sources = ref<SourceV2[]>([]);
const saveError = ref<string>('');

const supportedSchema = computed(() => supportedSchemaVersion(sources.value));
const flagsFor = computed(() => {
  const supported = supportedSchema.value;
  return (source: SourceV2) => sourceFlags(source, supported);
});

// Inline edit state (one row at a time).
const editingId = ref<number | null>(null);
const draftLabel = ref<string>('');
const draftBilling = ref<string>('');

function startEdit(source: SourceV2): void {
  saveError.value = '';
  editingId.value = source.id;
  draftLabel.value = source.token_label ?? '';
  draftBilling.value = source.billing_mode;
}
function cancelEdit(): void {
  editingId.value = null;
}

function skewText(seconds: number | null): string {
  if (seconds === null) return '—';
  const rounded = Math.round(seconds);
  return `${rounded >= 0 ? '+' : ''}${rounded}s`;
}

async function saveEdit(source: SourceV2): Promise<void> {
  const label = draftLabel.value.trim();
  const billing = draftBilling.value;
  // Billing mode drives cost attribution, so confirm before applying.
  if (
    billing !== source.billing_mode &&
    !window.confirm(
      `Change billing mode for "${source.name}" from ${source.billing_mode} to ${billing}? This affects how its cost is attributed.`
    )
  ) {
    return;
  }
  const index = sources.value.findIndex((s) => s.id === source.id);
  const original = sources.value[index];
  // Optimistic update.
  sources.value[index] = {
    ...original,
    token_label: label || null,
    billing_mode: billing,
  };
  editingId.value = null;
  try {
    const updated = await useClient().v2UpdateSource(source.id, {
      tokenLabel: label,
      billingMode: billing,
    });
    sources.value[index] = updated;
  } catch (e) {
    // Roll back on failure.
    sources.value[index] = original;
    saveError.value =
      e instanceof Error ? e.message : 'Failed to update the source.';
  }
}

async function load(): Promise<void> {
  await run(async () => {
    sources.value = await useClient().v2Sources();
  });
}

onMounted(() => {
  void load();
});
</script>

<template>
  <AsyncState
    :loading="loading && sources.length === 0"
    :error="error"
    :empty="!loading && sources.length === 0"
    empty-text="No reporting sources yet."
    @retry="retry"
  >
    <section class="card">
      <h3>Sources</h3>
      <p class="muted small note">
        Every collector and gateway reporting to this server, with freshness and
        schema health. Supported schema:
        {{ supportedSchema ?? '—' }}.
      </p>
      <p v-if="saveError" class="banner err">{{ saveError }}</p>

      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Machine</th>
            <th>Billing</th>
            <th>Health</th>
            <th class="num">Errors</th>
            <th>Schema</th>
            <th>Clock</th>
            <th>Last ingest</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="s in sources"
            :key="s.id"
            :class="{ stale: flagsFor(s).stale }"
          >
            <td>
              <div class="name">{{ s.token_label || s.name }}</div>
              <div class="muted sub">
                {{ s.type }}<span v-if="s.version"> · v{{ s.version }}</span>
              </div>
            </td>
            <td>{{ s.machine ?? '—' }}</td>
            <td>
              <template v-if="editingId === s.id">
                <select v-model="draftBilling">
                  <option v-for="m in BILLING_MODES" :key="m" :value="m">
                    {{ m }}
                  </option>
                </select>
              </template>
              <template v-else>{{ s.billing_mode }}</template>
            </td>
            <td>
              <span v-if="flagsFor(s).stale" class="badge stale-badge"
                >stale</span
              >
              <span v-else class="badge fresh-badge">fresh</span>
            </td>
            <td
              class="num tabular"
              :class="{ warn: s.health.recent_error_count > 0 }"
            >
              {{ s.health.recent_error_count }}
            </td>
            <td>
              <span :class="{ drift: flagsFor(s).schemaDrift }">
                {{ s.health.reported_schema_version ?? '—' }}
              </span>
              <span
                v-if="flagsFor(s).schemaDrift"
                class="badge drift-badge"
                title="Reporting an older ingest schema than the newest source"
                >drift</span
              >
            </td>
            <td
              class="tabular"
              :class="{ warn: flagsFor(s).clockSkew }"
              :title="`staleness threshold ${s.health.staleness_threshold_seconds}s`"
            >
              {{ skewText(s.health.clock_skew_seconds) }}
            </td>
            <td
              :title="
                s.health.last_successful_ingest
                  ? formatDateTime(s.health.last_successful_ingest)
                  : 'never'
              "
            >
              {{
                s.health.last_successful_ingest
                  ? timeAgo(s.health.last_successful_ingest)
                  : 'never'
              }}
            </td>
            <td class="actions">
              <template v-if="editingId === s.id">
                <input
                  v-model="draftLabel"
                  class="label-input"
                  placeholder="label"
                  aria-label="source label"
                />
                <button class="link" @click="saveEdit(s)">save</button>
                <button class="link muted" @click="cancelEdit">cancel</button>
              </template>
              <button v-else class="link" @click="startEdit(s)">edit</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </AsyncState>
</template>

<style scoped>
h3 {
  margin: 0 0 0.25rem;
}
.small {
  font-size: 0.78rem;
}
.note {
  margin: 0 0 1rem;
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
  vertical-align: top;
}
.num {
  text-align: right;
}
.name {
  font-weight: 600;
}
.sub {
  font-size: 0.75rem;
}
tr.stale td {
  background: color-mix(in srgb, var(--status-warning) 8%, transparent);
}
.badge {
  font-size: 0.68rem;
  font-weight: 600;
  padding: 0.05rem 0.4rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  text-transform: uppercase;
}
.stale-badge {
  color: var(--status-warning);
}
.fresh-badge {
  color: var(--status-good);
}
.drift-badge {
  color: var(--status-warning);
  margin-left: 0.35rem;
}
.drift {
  color: var(--status-warning);
  font-weight: 600;
}
.warn {
  color: var(--status-warning);
}
.actions {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
}
.link {
  font: inherit;
  background: none;
  border: none;
  color: var(--series-1);
  cursor: pointer;
  padding: 0;
}
.link.muted {
  color: var(--text-muted);
}
.label-input,
select {
  font: inherit;
  padding: 0.25rem 0.4rem;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
.label-input {
  width: 8rem;
}
.banner {
  padding: 0.5rem 0.8rem;
  border-radius: var(--radius);
  font-size: 0.85rem;
  margin-bottom: 0.75rem;
  border: 1px solid var(--border);
}
.banner.err {
  background: color-mix(in srgb, var(--status-critical) 14%, transparent);
  color: var(--status-critical);
}
</style>
