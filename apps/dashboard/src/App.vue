<script setup lang="ts">
// App shell: a token gate, top navigation, and the routed view. The token is
// required before any API call, so an empty token shows the gate.
import { ref } from 'vue';
import { useToken } from '@/composables/useApi';

const { token, setToken } = useToken();
const draft = ref('');

const links = [
  { to: '/', label: 'Now' },
  { to: '/trends', label: 'Trends' },
  { to: '/blocks', label: 'Blocks' },
  { to: '/breakdowns', label: 'Breakdowns' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/machines', label: 'Machines' },
  { to: '/report', label: 'Report' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/settings', label: 'Settings' },
];

function saveToken(): void {
  if (draft.value.trim()) {
    setToken(draft.value.trim());
  }
}
</script>

<template>
  <div v-if="!token" class="gate">
    <div class="card gate-card">
      <div class="gate-logo">
        <img
          class="brand-logo light"
          src="/logo-vertical-light.svg"
          alt="tokemetry"
        />
        <img
          class="brand-logo dark"
          src="/logo-vertical-dark.svg"
          alt="tokemetry"
        />
      </div>
      <p class="muted">Enter an API token to connect to your server.</p>
      <input
        v-model="draft"
        type="password"
        placeholder="tkm_…"
        @keyup.enter="saveToken"
      />
      <button @click="saveToken">Connect</button>
    </div>
  </div>

  <div v-else class="app">
    <header class="topbar">
      <RouterLink to="/" class="brand" aria-label="tokemetry home">
        <img
          class="brand-logo light"
          src="/logo-horizontal-light.svg"
          alt="tokemetry"
        />
        <img
          class="brand-logo dark"
          src="/logo-horizontal-dark.svg"
          alt="tokemetry"
        />
      </RouterLink>
      <nav>
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          class="navlink"
        >
          {{ link.label }}
        </RouterLink>
      </nav>
    </header>
    <main class="content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  padding: 0.75rem 1.5rem;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  position: sticky;
  top: 0;
  z-index: 10;
  flex-wrap: wrap;
}
.brand {
  display: flex;
  align-items: center;
}
.brand .brand-logo {
  height: 26px;
  display: block;
}
.gate-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 0.25rem;
}
.gate-logo .brand-logo {
  height: 96px;
}
nav {
  display: flex;
  gap: 0.25rem;
  flex-wrap: wrap;
}
.navlink {
  padding: 0.4rem 0.75rem;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 0.95rem;
}
.navlink.router-link-active {
  background: var(--gridline);
  color: var(--text-primary);
}
.content {
  padding: 1.5rem;
  max-width: 1200px;
  margin: 0 auto;
}
.gate {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 1.5rem;
}
.gate-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: min(420px, 100%);
}
input,
button {
  font: inherit;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--page);
  color: var(--text-primary);
}
button {
  cursor: pointer;
  font-weight: 600;
  background: var(--status-good);
  color: #fff;
  border: none;
}
</style>
