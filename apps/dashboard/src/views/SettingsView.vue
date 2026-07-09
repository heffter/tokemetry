<script setup lang="ts">
// Settings: theme, API token management, and the pricing table.
import { onMounted, ref } from 'vue';
import {
  applyTheme,
  storedTheme,
  useClient,
  useToken,
} from '@/composables/useApi';
import type { CreatedToken, TokenInfo } from '@/api/client';
import type { PricingRow } from '@/api/types';

const theme = ref(storedTheme());
const themes = ['system', 'light', 'dark'];
const tokens = ref<TokenInfo[]>([]);
const pricing = ref<PricingRow[]>([]);
const newLabel = ref('');
const minted = ref<CreatedToken | null>(null);
const error = ref('');
const { clearToken } = useToken();

function setTheme(value: string): void {
  theme.value = value;
  applyTheme(value);
}

async function load(): Promise<void> {
  try {
    const client = useClient();
    tokens.value = await client.listTokens();
    pricing.value = await client.pricing();
  } catch (e) {
    error.value = String(e);
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
    <h3>Pricing</h3>
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th class="num">Input /MTok</th>
          <th class="num">Output /MTok</th>
          <th>Source</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in pricing" :key="`${row.model}-${row.effective_date}`">
          <td>{{ row.model }}</td>
          <td class="num tabular">${{ row.input_per_mtok }}</td>
          <td class="num tabular">${{ row.output_per_mtok }}</td>
          <td>{{ row.source }}</td>
        </tr>
      </tbody>
    </table>
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
  margin: 0 0 1rem;
}
.toggle,
.mint {
  display: flex;
  gap: 0.5rem;
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
  flex: 1;
}
.minted code {
  word-break: break-all;
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
  font-size: 0.9rem;
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
