// App-wide access to the API token and a configured client.

import { ref } from 'vue';
import { ApiClient } from '@/api/client';

const TOKEN_KEY = 'tokemetry.token';
const THEME_KEY = 'tokemetry.theme';

const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '');

/** Reactive access to the stored API token plus setters. */
export function useToken() {
  function setToken(value: string): void {
    token.value = value;
    localStorage.setItem(TOKEN_KEY, value);
  }
  function clearToken(): void {
    token.value = '';
    localStorage.removeItem(TOKEN_KEY);
  }
  return { token, setToken, clearToken };
}

/** Build an API client bound to the current token. */
export function useClient(): ApiClient {
  return new ApiClient(token.value);
}

/** Apply and persist the color theme ('light' | 'dark' | 'system'). */
export function applyTheme(theme: string): void {
  const root = document.documentElement;
  if (theme === 'system') {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = theme;
  }
  localStorage.setItem(THEME_KEY, theme);
}

/** Read the persisted theme preference. */
export function storedTheme(): string {
  return localStorage.getItem(THEME_KEY) ?? 'system';
}
