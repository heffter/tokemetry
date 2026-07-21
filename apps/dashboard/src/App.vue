<script setup lang="ts">
// App shell: token gate, primary navigation, and the routed dashboard view.
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useToken } from '@/composables/useApi';

const { token, setToken } = useToken();
const route = useRoute();
const draft = ref('');
const mobileNavOpen = ref(false);

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

const current = computed(
  () => links.find((link) => link.to === route.path) ?? links[0]
);

function saveToken(): void {
  if (draft.value.trim()) {
    setToken(draft.value.trim());
  }
}

function closeMobileNav(): void {
  mobileNavOpen.value = false;
}
</script>

<template>
  <div v-if="!token" class="gate">
    <div class="gate-card">
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

      <div class="gate-copy">
        <p class="eyebrow">Server dashboard</p>
        <h1>Connect to tokemetry</h1>
        <p class="muted">
          Use a dashboard API token to open the operational view for usage,
          spend, limits, requests, and fleet health.
        </p>
      </div>

      <label class="field">
        <span>API token</span>
        <input
          v-model="draft"
          type="password"
          placeholder="tkm_..."
          autocomplete="current-password"
          @keyup.enter="saveToken"
        />
      </label>

      <button class="primary" @click="saveToken">Connect</button>
      <p class="muted footnote">Stored locally in this browser only.</p>
    </div>
  </div>

  <div v-else class="app">
    <button
      v-if="mobileNavOpen"
      class="nav-scrim"
      type="button"
      aria-label="Close navigation"
      @click="closeMobileNav"
    ></button>

    <aside class="sidebar">
      <div class="mobile-bar">
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

        <button
          class="menu-toggle"
          type="button"
          aria-label="Toggle navigation"
          :aria-expanded="mobileNavOpen"
          title="Navigation"
          @click="mobileNavOpen = !mobileNavOpen"
        >
          <span></span>
          <span></span>
          <span></span>
        </button>
      </div>

      <nav
        class="nav"
        :class="{ 'is-open': mobileNavOpen }"
        aria-label="Primary"
      >
        <template v-for="link in links" :key="link.to">
          <RouterLink
            :to="link.to"
            class="navlink"
            :title="link.section"
            @click="closeMobileNav"
          >
            {{ link.label }}
          </RouterLink>
        </template>
      </nav>

      <div class="sidebar-status">
        <span class="status-dot"></span>
        Connected
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">{{ current.section }}</p>
          <h1>{{ current.label }}</h1>
        </div>
        <RouterLink to="/settings" class="settings-link">Settings</RouterLink>
      </header>

      <main class="content">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
}

.workspace,
.content {
  min-width: 0;
}

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.25rem 1rem;
  border-right: 1px solid var(--border);
  background:
    linear-gradient(180deg, var(--surface-elevated), var(--surface) 72%),
    var(--surface);
}

.mobile-bar {
  display: contents;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.15rem 1.75rem;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
  background: color-mix(in srgb, var(--page) 86%, transparent);
  backdrop-filter: blur(16px);
}

.topbar h1 {
  margin: 0;
  font-size: clamp(1.4rem, 2vw, 2rem);
  line-height: 1.05;
}

.eyebrow {
  margin: 0 0 0.35rem;
  color: var(--text-muted);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand {
  display: flex;
  align-items: center;
  padding: 0.35rem 0.45rem 1rem;
}

.brand .brand-logo {
  height: 72px;
  max-width: 220px;
}

.menu-toggle,
.nav-scrim {
  display: none;
}

.gate-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 0.5rem;
}

.gate-logo .brand-logo {
  height: 92px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
  min-height: 0;
  overflow-y: auto;
}

.navlink {
  display: flex;
  align-items: center;
  min-height: 36px;
  padding: 0.48rem 0.65rem;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 0.92rem;
  font-weight: 650;
  transition:
    background 0.15s ease,
    color 0.15s ease,
    box-shadow 0.15s ease;
}

.navlink:hover {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.navlink.router-link-active {
  background: var(--nav-active);
  color: var(--text-primary);
  box-shadow: inset 3px 0 0 var(--accent);
}

.settings-link {
  display: inline-flex;
  align-items: center;
  min-height: 36px;
  padding: 0.45rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 0.9rem;
  font-weight: 700;
  box-shadow: var(--shadow-sm);
}

.content {
  padding: 1.5rem 1.75rem 2.5rem;
  max-width: 1380px;
  margin: 0 auto;
  width: 100%;
}

.gate {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background:
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--accent) 10%, transparent),
      transparent 34%
    ),
    radial-gradient(
      circle at 50% 100%,
      color-mix(in srgb, var(--status-good) 8%, transparent),
      transparent 42%
    ),
    var(--page);
}

.gate-card {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  width: min(440px, 100%);
  padding: 1.5rem;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--surface-elevated);
  box-shadow: var(--shadow-lg);
}

.gate-copy {
  text-align: center;
}

.gate-copy h1 {
  margin: 0;
  font-size: 1.75rem;
  line-height: 1.1;
}

.gate-copy .muted {
  margin: 0.7rem 0 0;
  line-height: 1.5;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  color: var(--text-secondary);
  font-size: 0.83rem;
  font-weight: 700;
}

.footnote {
  margin: 0;
  text-align: center;
  font-size: 0.78rem;
}

.sidebar-status {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin-top: auto;
  padding: 0.65rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 0.82rem;
  font-weight: 700;
  background: var(--surface-muted);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--status-good);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-good) 16%, transparent);
}

input,
button {
  font: inherit;
  padding: 0.65rem 0.75rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--input-bg);
  color: var(--text-primary);
}

button {
  cursor: pointer;
  font-weight: 750;
}

.primary {
  background: var(--accent);
  color: #fff;
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}

@media (max-width: 760px) {
  .app {
    display: block;
  }

  .sidebar {
    position: sticky;
    z-index: 20;
    height: auto;
    padding: 0;
    overflow: visible;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .mobile-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 76px;
    padding: 0.5rem 0.85rem;
    background: var(--surface-elevated);
  }

  .brand {
    padding: 0.2rem;
  }

  .brand .brand-logo {
    height: 58px;
    max-width: 236px;
  }

  .menu-toggle {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    flex: 0 0 48px;
    width: 48px;
    height: 48px;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }

  .menu-toggle span {
    display: block;
    width: 18px;
    height: 2px;
    border-radius: 999px;
    background: var(--text-primary);
  }

  .nav-scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 15;
    width: 100%;
    height: 100%;
    padding: 0;
    border: 0;
    border-radius: 0;
    background: rgba(15, 23, 42, 0.32);
  }

  .nav {
    display: none;
  }

  .nav.is-open {
    position: absolute;
    top: 100%;
    right: 0;
    left: 0;
    display: flex;
    max-height: calc(100vh - 76px);
    padding: 0.65rem 0.85rem 1rem;
    overflow-y: auto;
    border-bottom: 1px solid var(--border);
    background: var(--surface-elevated);
    box-shadow: var(--shadow-lg);
  }

  .navlink {
    min-height: 44px;
    padding: 0.65rem 0.75rem;
    white-space: normal;
  }

  .navlink.router-link-active {
    box-shadow: inset 3px 0 0 var(--accent);
  }

  .sidebar-status {
    display: none;
  }

  .topbar {
    position: static;
    padding: 1rem;
  }

  .settings-link {
    display: none;
  }

  .content {
    padding: 0.75rem;
  }
}
</style>
