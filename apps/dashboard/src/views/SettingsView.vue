<script setup lang="ts">
// Settings: theme, API token management, and pricing management (the screen
// that fixes the app-wide "cost n/a" problem).
import { onMounted, ref } from 'vue';
import {
  applyTheme,
  storedTheme,
  useClient,
  useToken,
} from '@/composables/useApi';
import { useAsync } from '@/composables/useAsync';
import {
  availableTimezones,
  browserTimezone,
  useSettings,
} from '@/composables/useSettings';
import type { CreatedToken, PriceRowInput, TokenInfo } from '@/api/client';
import type { PricingRow } from '@/api/types';
import { formatCost, formatDateTime, modelLabel } from '@/lib/format';
import { pricedCoverage } from '@/lib/coverage';

const theme = ref(storedTheme());
const themes = ['system', 'light', 'dark'];
const { timezonePref, setTimezone } = useSettings();
const timezones = availableTimezones();
// A live preview of "now" so the picker's effect is immediately visible.
const nowPreview = ref(new Date().toISOString());
const tokens = ref<TokenInfo[]>([]);
const pricing = ref<PricingRow[]>([]);
const unpricedModels = ref<string[]>([]);
const newLabel = ref('');
const minted = ref<CreatedToken | null>(null);
const priceStatus = ref('');
const { error, run } = useAsync();
const { clearToken } = useToken();

const today = new Date().toISOString().slice(0, 10);
const priceForm = ref<PriceRowInput>(blankPrice());

function blankPrice(): PriceRowInput {
  return {
    provider: 'anthropic',
    model: '',
    effective_date: today,
    input_per_mtok: '',
    output_per_mtok: '',
    cache_read_per_mtok: '',
    cache_write_short_per_mtok: '',
    cache_write_long_per_mtok: '',
    source: 'manual',
  };
}

function setTheme(value: string): void {
  theme.value = value;
  applyTheme(value);
}

async function load(): Promise<void> {
  await run(async () => {
    const client = useClient();
    tokens.value = await client.listTokens();
    pricing.value = await client.pricing();
    const byModel = (await client.usage({ groupBy: 'model' })).buckets;
    unpricedModels.value = pricedCoverage(byModel).unpricedKeys;
  });
}

function prefill(model: string): void {
  priceForm.value = { ...blankPrice(), model };
  priceStatus.value = '';
}

async function addPrice(): Promise<void> {
  const f = priceForm.value;
  if (!f.model.trim() || !f.input_per_mtok || !f.output_per_mtok) {
    priceStatus.value = 'model, input and output prices are required';
    return;
  }
  try {
    await useClient().createPrice(f);
    const result = await useClient().recomputeCosts();
    priceStatus.value = `added ${f.model}; repriced ${result.events_updated} events`;
    priceForm.value = blankPrice();
    await load();
  } catch (e) {
    priceStatus.value = String(e);
  }
}

async function syncLitellm(): Promise<void> {
  priceStatus.value = 'syncing from LiteLLM…';
  try {
    const synced = await useClient().syncLitellm();
    const result = await useClient().recomputeCosts();
    priceStatus.value = `synced ${synced.synced} prices; repriced ${result.events_updated} events`;
    await load();
  } catch (e) {
    priceStatus.value = String(e);
  }
}

async function recompute(): Promise<void> {
  priceStatus.value = 'recomputing…';
  try {
    const result = await useClient().recomputeCosts();
    priceStatus.value = `repriced ${result.events_updated} events`;
    await load();
  } catch (e) {
    priceStatus.value = String(e);
  }
}

async function mint(): Promise<void> {
  if (!newLabel.value.trim()) return;
  minted.value = await useClient().createToken(newLabel.value.trim());
  newLabel.value = '';
  await load();
}

async function revoke(label: string): Promise<void> {
  await useClient().revokeToken(label);
  await load();
}

onMounted(load);
</script>

<template>
  <section class="card">
    <h3>Appearance</h3>
    <div class="toggle">
      <button
        v-for="t in themes"
        :key="t"
        :class="{ active: theme === t }"
        @click="setTheme(t)"
      >
        {{ t }}
      </button>
    </div>
    <div class="tzrow">
      <label for="tz">Timezone</label>
      <select
        id="tz"
        :value="timezonePref"
        @change="setTimezone(($event.target as HTMLSelectElement).value)"
      >
        <option value="">Auto — browser ({{ browserTimezone() }})</option>
        <option v-for="tz in timezones" :key="tz" :value="tz">{{ tz }}</option>
      </select>
      <span class="muted">now: {{ formatDateTime(nowPreview) }}</span>
    </div>
  </section>

  <section class="card">
    <div class="head">
      <h3>Pricing</h3>
      <div class="toggle">
        <button @click="syncLitellm">Sync from LiteLLM</button>
        <button @click="recompute">Recompute costs</button>
      </div>
    </div>

    <div v-if="unpricedModels.length" class="banner">
      <strong>{{ unpricedModels.length }}</strong> model(s) in use have no price
      (cost shows as unpriced):
      <span
        v-for="m in unpricedModels"
        :key="m"
        class="chip"
        @click="prefill(m)"
      >
        {{ modelLabel(m) }} — add price
      </span>
    </div>

    <div class="form">
      <input v-model="priceForm.model" placeholder="model id" class="wide" />
      <input v-model="priceForm.input_per_mtok" placeholder="input /MTok" />
      <input v-model="priceForm.output_per_mtok" placeholder="output /MTok" />
      <input
        v-model="priceForm.cache_read_per_mtok"
        placeholder="cache read /MTok"
      />
      <input
        v-model="priceForm.cache_write_short_per_mtok"
        placeholder="cache write 5m"
      />
      <input
        v-model="priceForm.cache_write_long_per_mtok"
        placeholder="cache write 1h"
      />
      <button @click="addPrice">Add / override</button>
    </div>
    <div v-if="priceStatus" class="muted status">{{ priceStatus }}</div>

    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>Effective</th>
            <th class="num">Input</th>
            <th class="num">Output</th>
            <th class="num">Cache read</th>
            <th class="num">Write 5m</th>
            <th class="num">Write 1h</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in pricing"
            :key="`${row.provider}-${row.model}-${row.effective_date}`"
          >
            <td>{{ row.provider }}</td>
            <td>{{ modelLabel(row.model) }}</td>
            <td class="tabular">{{ row.effective_date }}</td>
            <td class="num tabular">{{ formatCost(row.input_per_mtok) }}</td>
            <td class="num tabular">{{ formatCost(row.output_per_mtok) }}</td>
            <td class="num tabular">
              {{ formatCost(row.cache_read_per_mtok) }}
            </td>
            <td class="num tabular">
              {{ formatCost(row.cache_write_short_per_mtok) }}
            </td>
            <td class="num tabular">
              {{ formatCost(row.cache_write_long_per_mtok) }}
            </td>
            <td>{{ row.source }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h3>API tokens</h3>
    <div v-if="error" class="muted">{{ error }}</div>
    <div class="mint">
      <input v-model="newLabel" placeholder="label (e.g. openclaw)" />
      <button @click="mint">Create</button>
    </div>
    <p v-if="minted" class="minted">
      New token for <strong>{{ minted.label }}</strong> (shown once):
      <code>{{ minted.token }}</code>
    </p>
    <ul class="tokens">
      <li v-for="tk in tokens" :key="tk.label">
        <span>{{ tk.label }}</span>
        <span class="muted">{{ tk.revoked ? 'revoked' : 'active' }}</span>
        <button v-if="!tk.revoked" class="link" @click="revoke(tk.label)">
          revoke
        </button>
      </li>
    </ul>
  </section>

  <section class="card">
    <button class="danger" @click="clearToken">
      Disconnect (forget token)
    </button>
  </section>
</template>

<style scoped>
section {
  margin-bottom: 1.25rem;
}
h3 {
  margin: 0;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.toggle,
.mint,
.form {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.tzrow {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.9rem;
  flex-wrap: wrap;
}
.tzrow select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  max-width: 260px;
}
.form {
  margin: 0.75rem 0;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-secondary);
  cursor: pointer;
}
button.active {
  background: var(--gridline);
  color: var(--text-primary);
}
input {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
  width: 120px;
}
input.wide {
  width: 220px;
  flex: 1;
}
.banner {
  background: color-mix(in srgb, var(--status-warning) 12%, transparent);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.9rem;
}
.chip {
  display: inline-block;
  margin: 0.15rem 0.25rem 0 0;
  padding: 0.1rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
  background: var(--surface);
}
.status {
  font-size: 0.85rem;
}
.minted code {
  word-break: break-all;
}
.scroll {
  overflow-x: auto;
}
.tokens {
  list-style: none;
  padding: 0;
  margin: 1rem 0 0;
}
.tokens li {
  display: flex;
  gap: 1rem;
  align-items: center;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--border);
}
.link {
  border: none;
  background: none;
  color: var(--status-critical);
  cursor: pointer;
  margin-left: auto;
}
.danger {
  color: var(--status-critical);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  white-space: nowrap;
}
th,
td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.num {
  text-align: right;
}
</style>
