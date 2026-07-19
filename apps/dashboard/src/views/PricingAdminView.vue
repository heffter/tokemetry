<script setup lang="ts">
// Provider-neutral pricing administration on /api/v2/pricing. Rate-card table
// with effective ranges, tier/mode/bracket/priority/source; manual rate
// creation and closure; a LiteLLM + curated import rendered as a dry-run diff
// with an explicit apply step (D-015); a reprice launcher and revert; and the
// unpriced / unknown-model reports. Every mutation confirms and shows its audit
// outcome (the new pricing-state version). Mutations need an admin:pricing
// token; a 403 surfaces as an error rather than silently failing.
import { computed, onMounted, ref } from 'vue';
import AsyncState from '@/components/AsyncState.vue';
import { useClient } from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import { formatCost } from '@/lib/format';
import { presetRange } from '@/lib/filters';
import type {
  ImportResponse,
  RateCardCreate,
  RateCardV2,
  UnknownModelReportRow,
  UnpricedReportRow,
} from '@/api/types-v2';

const { loading, error, run, retry } = useAsync();
const rateCards = ref<RateCardV2[]>([]);
const unpriced = ref<UnpricedReportRow[]>([]);
const unknownModels = ref<UnknownModelReportRow[]>([]);
const status = ref<string>('');
const failure = ref<string>('');
const busy = ref(false);

function blankCard(): RateCardCreate {
  return {
    provider: '',
    native_model: '',
    unit_type: 'input',
    effective_from: presetRange('today').to,
    unit_price: '',
    mode: 'realtime',
    source: 'manual',
    priority: 0,
    override: false,
  };
}
const createForm = ref<RateCardCreate>(blankCard());

const importDiff = ref<ImportResponse | null>(null);
const reprice = ref({ start: '', end: '', provider: '', native_model: '' });

function note(message: string): void {
  status.value = message;
  failure.value = '';
}
function fail(e: unknown): void {
  failure.value = e instanceof Error ? e.message : 'Request failed.';
  status.value = '';
}

async function refreshCards(): Promise<void> {
  rateCards.value = await useClient().v2Pricing();
}

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    const [cards, up, um] = await Promise.all([
      client.v2Pricing(),
      client.v2UnpricedReport().catch(() => []),
      client.v2UnknownModelsReport().catch(() => []),
    ]);
    rateCards.value = cards;
    unpriced.value = up;
    unknownModels.value = um;
  });
}

async function createCard(): Promise<void> {
  if (
    !createForm.value.provider ||
    !createForm.value.native_model ||
    !createForm.value.unit_price
  ) {
    fail(new Error('Provider, model, and unit price are required.'));
    return;
  }
  busy.value = true;
  try {
    const res = await useClient().v2CreatePrice(createForm.value);
    note(
      `Created rate card #${res.rate_card.id} (pricing version ${res.pricing_version}).`
    );
    createForm.value = blankCard();
    await refreshCards();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = false;
  }
}

async function closeCard(card: RateCardV2): Promise<void> {
  const effectiveTo = window.prompt(
    `Close rate card #${card.id} (${card.provider}/${card.native_model} ${card.unit_type}) effective on (YYYY-MM-DD):`,
    presetRange('today').to
  );
  if (!effectiveTo) return;
  busy.value = true;
  try {
    const res = await useClient().v2ClosePrice(card.id, effectiveTo);
    note(
      `Closed rate card #${res.rate_card_id} (pricing version ${res.pricing_version}).`
    );
    await refreshCards();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = false;
  }
}

async function previewImport(): Promise<void> {
  busy.value = true;
  importDiff.value = null;
  try {
    importDiff.value = await useClient().v2ImportPricing(true);
    note(
      `Dry run: ${importDiff.value.new} new, ${importDiff.value.superseded} superseded, ${importDiff.value.conflicts} conflict(s).`
    );
  } catch (e) {
    fail(e);
  } finally {
    busy.value = false;
  }
}

async function applyImport(): Promise<void> {
  const diff = importDiff.value;
  if (!diff) return;
  if (
    !window.confirm(
      `Apply this import? ${diff.new} new and ${diff.superseded} superseded rate card(s) will be written.`
    )
  ) {
    return;
  }
  busy.value = true;
  try {
    const res = await useClient().v2ImportPricing(false, diff.digest);
    note(`Applied import: ${res.new} new, ${res.superseded} superseded.`);
    importDiff.value = null;
    await refreshCards();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = false;
  }
}

async function runReprice(): Promise<void> {
  const r = reprice.value;
  if (!r.start || !r.end) {
    fail(new Error('Reprice needs a start and end date.'));
    return;
  }
  if (
    !window.confirm(
      `Reprice costs from ${r.start} to ${r.end}${r.provider ? ` for ${r.provider}` : ''}? Prior rows are retained under a new version.`
    )
  ) {
    return;
  }
  busy.value = true;
  try {
    const res = await useClient().v2Reprice({
      start: `${r.start}T00:00:00Z`,
      end: `${r.end}T00:00:00Z`,
      provider: r.provider || undefined,
      native_model: r.native_model || undefined,
    });
    note(
      `Repriced ${res.affected} cost(s) under pricing version ${res.pricing_version}.`
    );
  } catch (e) {
    fail(e);
  } finally {
    busy.value = false;
  }
}

function fmtRange(card: RateCardV2): string {
  return `${card.effective_from} → ${card.effective_to ?? 'open'}`;
}

const sortedCards = computed(() =>
  [...rateCards.value].sort(
    (a, b) =>
      a.provider.localeCompare(b.provider) ||
      a.native_model.localeCompare(b.native_model) ||
      a.unit_type.localeCompare(b.unit_type)
  )
);

onMounted(() => {
  void load();
});
</script>

<template>
  <div>
    <p v-if="status" class="banner ok">{{ status }}</p>
    <p v-if="failure" class="banner err">{{ failure }}</p>
    <p class="muted small note">
      Pricing changes need an admin:pricing token. Every mutation is audited and
      returns the new pricing-state version.
    </p>

    <AsyncState
      :loading="loading && rateCards.length === 0"
      :error="error"
      @retry="retry"
    >
      <section class="card">
        <h3>Create rate card</h3>
        <div class="form">
          <input
            v-model="createForm.provider"
            placeholder="provider"
            aria-label="provider"
          />
          <input
            v-model="createForm.native_model"
            placeholder="native model"
            aria-label="native model"
          />
          <input
            v-model="createForm.unit_type"
            placeholder="unit type"
            aria-label="unit type"
          />
          <input
            v-model="createForm.effective_from"
            type="date"
            aria-label="effective from"
          />
          <input
            v-model="createForm.unit_price"
            placeholder="unit price"
            aria-label="unit price"
          />
          <input
            v-model.number="createForm.priority"
            type="number"
            placeholder="priority"
            aria-label="priority"
          />
          <label class="chk">
            <input v-model="createForm.override" type="checkbox" /> override
          </label>
          <button :disabled="busy" @click="createCard">Create</button>
        </div>
      </section>

      <section class="card">
        <div class="head">
          <h3>Rate cards</h3>
          <span class="muted small">{{ sortedCards.length }} cards</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Unit</th>
              <th class="num">Price</th>
              <th>Effective</th>
              <th>Tier / mode</th>
              <th class="num">Prio</th>
              <th>Source</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in sortedCards" :key="c.id">
              <td>{{ c.provider }}</td>
              <td class="mono">{{ c.native_model }}</td>
              <td>{{ c.unit_type }}</td>
              <td class="num tabular">{{ formatCost(c.unit_price) }}</td>
              <td>{{ fmtRange(c) }}</td>
              <td class="muted">
                {{ c.service_tier ?? '—' }} / {{ c.mode
                }}<span v-if="c.context_bracket">
                  · {{ c.context_bracket }}</span
                >
              </td>
              <td class="num tabular">
                {{ c.priority }}<span v-if="c.override" class="badge">ovr</span>
              </td>
              <td class="muted">{{ c.source }}</td>
              <td>
                <button
                  v-if="!c.effective_to"
                  class="link"
                  :disabled="busy"
                  @click="closeCard(c)"
                >
                  close
                </button>
              </td>
            </tr>
            <tr v-if="sortedCards.length === 0">
              <td colspan="9" class="muted">No rate cards yet.</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="card">
        <h3>Import prices (LiteLLM + curated)</h3>
        <div class="actions">
          <button :disabled="busy" @click="previewImport">
            Preview import
          </button>
          <button
            v-if="importDiff"
            class="primary"
            :disabled="busy"
            @click="applyImport"
          >
            Apply ({{ importDiff.new }} new,
            {{ importDiff.superseded }} superseded)
          </button>
        </div>
        <div v-if="importDiff" class="diff">
          <p class="muted small">
            digest {{ importDiff.digest.slice(0, 12) }} ·
            {{ importDiff.unchanged }} unchanged ·
            {{ importDiff.conflicts }} conflict(s)
          </p>
          <table>
            <thead>
              <tr>
                <th>Action</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Unit</th>
                <th class="num">New price</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(ch, i) in importDiff.changes.slice(0, 100)" :key="i">
                <td>
                  <span class="badge" :class="`act-${ch.action}`">{{
                    ch.action
                  }}</span>
                </td>
                <td>{{ ch.provider }}</td>
                <td class="mono">{{ ch.native_model }}</td>
                <td>{{ ch.unit_type }}</td>
                <td class="num tabular">
                  {{ ch.new_price === null ? '—' : formatCost(ch.new_price) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="card">
        <h3>Reprice a range</h3>
        <div class="form">
          <input
            v-model="reprice.start"
            type="date"
            aria-label="reprice start"
          />
          <input v-model="reprice.end" type="date" aria-label="reprice end" />
          <input
            v-model="reprice.provider"
            placeholder="provider (optional)"
            aria-label="reprice provider"
          />
          <input
            v-model="reprice.native_model"
            placeholder="model (optional)"
            aria-label="reprice model"
          />
          <button :disabled="busy" @click="runReprice">Reprice</button>
        </div>
      </section>

      <section class="grid two">
        <div class="card">
          <h3>Unpriced / partial usage</h3>
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Status</th>
                <th class="num">Events</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in unpriced" :key="i">
                <td>{{ r.provider }}</td>
                <td class="mono">{{ r.native_model }}</td>
                <td>{{ r.cost_status }}</td>
                <td class="num tabular">{{ r.event_count }}</td>
              </tr>
              <tr v-if="unpriced.length === 0">
                <td colspan="4" class="muted">Everything priced.</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card">
          <h3>Unknown models</h3>
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th class="num">Seen</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in unknownModels" :key="i">
                <td>{{ r.provider }}</td>
                <td class="mono">{{ r.native_model }}</td>
                <td class="num tabular">{{ r.observations }}</td>
              </tr>
              <tr v-if="unknownModels.length === 0">
                <td colspan="3" class="muted">No unknown models.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </AsyncState>
  </div>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0 0 0.75rem;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.two {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}
.form {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}
.actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
input,
select,
button {
  font: inherit;
  padding: 0.35rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
input {
  min-width: 7rem;
}
button {
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button.primary {
  background: var(--status-good);
  color: #fff;
  border: none;
}
.chk {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.85rem;
}
.chk input {
  min-width: 0;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
th,
td {
  text-align: left;
  padding: 0.4rem 0.55rem;
  border-bottom: 1px solid var(--border);
}
.num {
  text-align: right;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.link {
  background: none;
  border: none;
  color: var(--series-1);
  padding: 0;
}
.badge {
  font-size: 0.66rem;
  font-weight: 600;
  padding: 0.05rem 0.35rem;
  border-radius: 5px;
  border: 1px solid var(--border);
  margin-left: 0.3rem;
}
.act-new {
  color: var(--status-good);
}
.act-conflict {
  color: var(--status-critical);
}
.act-superseded {
  color: var(--status-warning);
}
.diff {
  margin-top: 0.75rem;
}
.small {
  font-size: 0.78rem;
}
.note {
  margin: 0 0 1rem;
}
.banner {
  padding: 0.5rem 0.8rem;
  border-radius: var(--radius);
  font-size: 0.88rem;
  margin-bottom: 0.75rem;
  border: 1px solid var(--border);
}
.banner.ok {
  background: color-mix(in srgb, var(--status-good) 14%, transparent);
}
.banner.err {
  background: color-mix(in srgb, var(--status-critical) 14%, transparent);
  color: var(--status-critical);
}
</style>
