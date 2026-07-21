<script setup lang="ts">
// Cost view built on /api/v2/costs. The two cost metrics -- actual API spend
// and subscription-equivalent value -- are always presented as two distinct
// series and never summed (FR-COST-012, D-007). Cost rows surface their
// pricing completeness (priced/partial/unpriced, FR-UI-008), and a
// reconciliation panel shows observed-vs-computed drift per provider
// (FR-UI-013). The costs endpoint is range-bounded, so a long selection is
// clamped to the most recent window and flagged.
import { computed, onMounted, ref } from 'vue';
import EChart from '@/components/EChart.vue';
import AsyncState from '@/components/AsyncState.vue';
import FilterBar from '@/components/FilterBar.vue';
import LabeledValue from '@/components/LabeledValue.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { useGlobalFilters } from '@/composables/useGlobalFilters';
import { groupedBarOption } from '@/lib/charts';
import { formatCost } from '@/lib/format';
import { resolveMoneyState } from '@/lib/valueState';
import { sumCostRows, costStatusOf } from '@/lib/costs';
import type { CostStatus } from '@/lib/costs';
import { knownModelIds, resolveModel } from '@/lib/modelRegistry';
import {
  clampRangeDays,
  dayEndIso,
  dayStartIso,
  enumerateDays,
  presetRange,
} from '@/lib/filters';
import type { UsageFilter } from '@/lib/filters';
import type {
  CostRowV2,
  ModelV2,
  ProviderV2,
  QueryWarning,
  ReconciliationRowV2,
} from '@/api/types-v2';

// The v2 costs endpoint bounds the range; keep a margin under the server's
// default (366 days) so a clamped request is always accepted.
const MAX_RANGE_DAYS = 365;

const { loading, error, run, retry } = useAsync();
const { provider: globalProvider } = useGlobalFilters();
const byProvider = ref<CostRowV2[]>([]);
const byDay = ref<CostRowV2[]>([]);
const warnings = ref<QueryWarning[]>([]);
const reconciliation = ref<ReconciliationRowV2[]>([]);
const providers = ref<ProviderV2[]>([]);
const models = ref<ModelV2[]>([]);
const machines = ref<string[]>([]);
const projects = ref<string[]>([]);
const filter = ref<UsageFilter>(presetRange('30d'));
const clampedFrom = ref<string | null>(null);

const knownIds = computed(() => knownModelIds(models.value));
const providerName = computed(() => {
  const map = new Map(providers.value.map((p) => [p.id, p.display_name]));
  return (id: string): string => map.get(id) ?? id;
});
const providerOptions = computed(() =>
  providers.value.map((p) => ({ value: p.id, label: p.display_name }))
);
const modelOptions = computed(() =>
  models.value
    .filter((m) => !globalProvider.value || m.provider === globalProvider.value)
    .map((m) => ({
      value: m.native_model_id,
      label: resolveModel(m.native_model_id, knownIds.value).display,
    }))
);

const money = (value: unknown): string => formatCost(String(value));

const totals = computed(() => sumCostRows(byProvider.value));
const actualState = computed(() =>
  resolveMoneyState(totals.value.actualSpend.toFixed(10), { format: money })
);
const subscriptionState = computed(() =>
  resolveMoneyState(totals.value.subscriptionValue.toFixed(10), {
    format: money,
  })
);

/** "2026-06-01" -> "Jun 1" (UTC). */
function shortDay(key: string): string {
  const date = new Date(`${key}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? key
    : date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        timeZone: 'UTC',
      });
}

// Daily dual-metric trend: two grouped (never stacked) series, gap-filled so
// days with no spend read as zero rather than collapsing the axis.
const dailyChart = computed(() => {
  const rows = byDay.value;
  if (rows.length === 0) return groupedBarOption([], []);
  const byKey = new Map(rows.map((r) => [r.key, r]));
  const keys = [...byKey.keys()].sort();
  const days = enumerateDays(keys[0], keys[keys.length - 1]);
  const actual = days.map((d) => Number(byKey.get(d)?.actual_spend_usd ?? 0));
  const subscription = days.map((d) =>
    Number(byKey.get(d)?.subscription_value_usd ?? 0)
  );
  return groupedBarOption(
    days.map(shortDay),
    [
      { name: 'API spend', values: actual },
      { name: 'Subscription value', values: subscription },
    ],
    { valueFormatter: money }
  );
});

const providerRows = computed(() =>
  [...byProvider.value].sort(
    (a, b) => Number(b.actual_spend_usd) - Number(a.actual_spend_usd)
  )
);

const STATUS_LABEL: Record<CostStatus, string> = {
  priced: 'priced',
  partial: 'partial',
  unpriced: 'unpriced',
};

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const f = filter.value;
    const fallback = presetRange('30d');
    const clamped = clampRangeDays(
      f.from ?? fallback.from,
      f.to ?? fallback.to,
      MAX_RANGE_DAYS
    );
    clampedFrom.value = clamped.clamped ? clamped.from : null;
    const from = dayStartIso(clamped.from);
    // Inclusive end-of-day so the current day's costs are counted, not dropped.
    const to = dayEndIso(clamped.to);
    const q = {
      from,
      to,
      provider: f.provider,
      model: f.model,
      machine: f.machine,
      project: f.project,
    };
    const [prov, day, recon] = await Promise.all([
      client.v2Costs({ ...q, groupBy: 'provider' }),
      client.v2Costs({ ...q, groupBy: 'day' }),
      client.v2Reconciliation(q),
    ]);
    byProvider.value = prov.rows;
    byDay.value = day.rows;
    warnings.value = prov.warnings;
    reconciliation.value = recon.rows;
  });
}

async function loadOptions(): Promise<void> {
  // Filter chrome is non-critical; a failure must not blank the page.
  try {
    const client = useClient();
    providers.value = await client.v2Providers();
    models.value = await client.v2Models();
    machines.value = (await client.machines()).map((m) => m.id);
    const all = presetRange('all');
    projects.value = (
      await client.usage({ groupBy: 'project', ...all })
    ).buckets
      .filter((b) => b.key)
      .sort((a, b) => b.total_tokens - a.total_tokens)
      .map((b) => b.key);
  } catch {
    projects.value = [];
  }
}

function onFilter(next: UsageFilter): void {
  filter.value = next;
  void load();
}

onMounted(() => {
  void loadOptions();
  void load();
});
</script>

<template>
  <div>
    <FilterBar
      :providers="providerOptions"
      :models="modelOptions"
      :machines="machines"
      :projects="projects"
      @change="onFilter"
    />
    <p v-if="clampedFrom" class="muted note">
      Range clamped to the most recent {{ MAX_RANGE_DAYS }} days (from
      {{ clampedFrom }}) — cost queries are bounded.
    </p>

    <AsyncState
      :loading="loading && byProvider.length === 0"
      :error="error"
      @retry="retry"
    >
      <section class="grid tiles">
        <LabeledValue
          label="API spend"
          :state="actualState"
          sub="actual pay-per-token cost"
        />
        <LabeledValue
          label="Subscription value"
          :state="subscriptionState"
          sub="equivalent value at list price"
        />
        <LabeledValue
          v-if="totals.unpricedEvents > 0"
          label="Unpriced events"
          :state="{ kind: 'value', text: String(totals.unpricedEvents) }"
          sub="cost incomplete — add rate cards"
        />
      </section>
      <p class="muted note">
        API spend and subscription-equivalent value are shown as two separate
        metrics and never summed — one is money out, the other is plan value.
      </p>

      <section v-for="w in warnings" :key="w.kind" class="banner warn">
        {{ w.detail }} ({{ w.count }})
      </section>

      <section class="card">
        <h3>Daily cost (API spend vs subscription value)</h3>
        <EChart :option="dailyChart" height="300px" />
      </section>

      <section class="card">
        <h3>By provider</h3>
        <div class="provider-list">
          <article
            v-for="row in providerRows"
            :key="row.key"
            class="provider-card"
          >
            <div class="provider-card-head">
              <strong>{{ providerName(row.key) }}</strong>
              <span class="badge" :class="`status-${costStatusOf(row)}`">
                {{ STATUS_LABEL[costStatusOf(row)] }}
              </span>
            </div>
            <dl class="provider-values">
              <div>
                <dt>API spend</dt>
                <dd>{{ formatCost(row.actual_spend_usd) }}</dd>
              </div>
              <div>
                <dt>Subscription value</dt>
                <dd>{{ formatCost(row.subscription_value_usd) }}</dd>
              </div>
              <div>
                <dt>Pricing</dt>
                <dd class="muted">{{ row.pricing_version }}</dd>
              </div>
            </dl>
          </article>
          <p v-if="providerRows.length === 0" class="muted">
            No cost data in this range.
          </p>
        </div>
        <div class="table-scroll provider-table">
          <table class="data">
            <thead>
              <tr>
                <th>Provider</th>
                <th class="num">API spend</th>
                <th class="num">Subscription value</th>
                <th>Status</th>
                <th>Pricing</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in providerRows" :key="row.key">
                <td>{{ providerName(row.key) }}</td>
                <td class="num tabular">
                  {{ formatCost(row.actual_spend_usd) }}
                </td>
                <td class="num tabular">
                  {{ formatCost(row.subscription_value_usd) }}
                </td>
                <td>
                  <span
                    class="badge"
                    :class="`status-${costStatusOf(row)}`"
                    :title="
                      row.unpriced_event_count > 0
                        ? `${row.unpriced_event_count} unpriced event(s)`
                        : ''
                    "
                  >
                    {{ STATUS_LABEL[costStatusOf(row)] }}
                  </span>
                </td>
                <td class="muted">{{ row.pricing_version }}</td>
              </tr>
              <tr v-if="providerRows.length === 0">
                <td colspan="5" class="muted">No cost data in this range.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card">
        <h3>Cost reconciliation (observed vs computed)</h3>
        <div v-if="reconciliation.length" class="table-scroll">
          <table class="data">
            <thead>
              <tr>
                <th>Provider</th>
                <th class="num">Computed</th>
                <th class="num">Observed</th>
                <th class="num">Drift</th>
                <th class="num">Events</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in reconciliation" :key="row.provider">
                <td>{{ providerName(row.provider) }}</td>
                <td class="num tabular">{{ formatCost(row.computed_usd) }}</td>
                <td class="num tabular">{{ formatCost(row.observed_usd) }}</td>
                <td class="num tabular">{{ formatCost(row.drift_usd) }}</td>
                <td class="num tabular">{{ row.event_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted">
          No observed costs to reconcile yet — populated once exporters report
          provider-billed costs.
        </p>
      </section>
    </AsyncState>
  </div>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
.tiles {
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}
h3 {
  margin: 0 0 1rem;
}
.note {
  font-size: 0.85rem;
  margin: 0 0 1rem;
}
.data {
  min-width: 560px;
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}
.provider-list {
  display: none;
}
.data th,
.data td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.data th.num,
.data td.num {
  text-align: right;
}
.badge {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.1rem 0.4rem;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.status-partial {
  color: var(--status-warning);
}
.status-unpriced {
  color: var(--status-critical);
}
.banner {
  padding: 0.6rem 0.9rem;
  border-radius: var(--radius);
  font-size: 0.9rem;
  border: 1px solid var(--border);
}
.banner.warn {
  background: color-mix(in srgb, var(--status-warning) 14%, transparent);
}

@media (max-width: 760px) {
  .provider-list {
    display: grid;
    gap: 0.65rem;
  }
  .provider-list > p {
    margin: 0;
  }
  .provider-table {
    display: none;
  }
  .provider-card {
    padding: 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-muted);
  }
  .provider-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .provider-values {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.65rem 1rem;
    margin: 0.75rem 0 0;
  }
  .provider-values div:last-child {
    grid-column: 1 / -1;
  }
  .provider-values dt {
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
  }
  .provider-values dd {
    margin: 0.18rem 0 0;
    font-variant-numeric: tabular-nums;
    font-weight: 700;
  }
}
</style>
