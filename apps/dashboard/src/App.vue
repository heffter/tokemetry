<script setup lang="ts">
// App shell: a token gate, top navigation, and the routed view. The token is
// required before any API call, so an empty token shows the gate.
import { ref } from 'vue';
import { useToken } from '@/composables/useApi';

const { token, setToken } = useToken();
const draft = ref('');

// Grouped into logical sections (Overview, Usage, Costs, Requests, Sessions,
// Limits, Fleet, Quality, Reports, Alerts, Settings). Every pre-existing route
// is kept (D-017); the section labels order the flat nav without hiding routes.
const links = [
  { to: '/', label: 'Now', section: 'Overview' },
  { to: '/trends', label: 'Trends', section: 'Usage' },
  { to: '/breakdowns', label: 'Breakdowns', section: 'Usage' },
  { to: '/costs', label: 'Costs', section: 'Costs' },
  { to: '/requests', label: 'Requests', section: 'Requests' },
  { to: '/sessions', label: 'Sessions', section: 'Sessions' },
  { to: '/blocks', label: 'Blocks', section: 'Limits' },
  { to: '/limits', label: 'Limits', section: 'Limits' },
  { to: '/machines', label: 'Machines', section: 'Fleet' },
  { to: '/sources', label: 'Sources', section: 'Fleet' },
  { to: '/data-quality', label: 'Data quality', section: 'Quality' },
  { to: '/pricing-admin', label: 'Pricing', section: 'Quality' },
  { to: '/report', label: 'Report', section: 'Reports' },
  { to: '/alerts', label: 'Alerts', section: 'Alerts' },
  { to: '/settings', label: 'Settings', section: 'Settings' },
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
        <template v-for="(link, i) in links" :key="link.to">
          <span
            v-if="i > 0 && links[i - 1].section !== link.section"
            class="nav-sep"
            aria-hidden="true"
          ></span>
          <RouterLink :to="link.to" class="navlink" :title="link.section">
            {{ link.label }}
          </RouterLink>
        </template>
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
.nav-sep {
  width: 1px;
  align-self: stretch;
  margin: 0.15rem 0.15rem;
  background: var(--border);
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
