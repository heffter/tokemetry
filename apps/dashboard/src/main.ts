import { createApp } from 'vue';
import App from './App.vue';
import { router } from './router';
import { applyTheme, storedTheme } from './composables/useApi';
import './styles/theme.css';

applyTheme(storedTheme());
createApp(App).use(router).mount('#app');
