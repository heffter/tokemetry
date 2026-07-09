import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

// Test-only config. The unit tests are pure TypeScript (no .vue imports), so
// the Vue plugin is intentionally not loaded here.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
  },
});
