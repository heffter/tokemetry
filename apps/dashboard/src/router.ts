import { createRouter, createWebHistory } from 'vue-router';

// Lazy-loaded views keep the initial bundle small.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'now', component: () => import('@/views/NowView.vue') },
    {
      path: '/trends',
      name: 'trends',
      component: () => import('@/views/TrendsView.vue'),
    },
    {
      path: '/blocks',
      name: 'blocks',
      component: () => import('@/views/BlocksView.vue'),
    },
    {
      path: '/breakdowns',
      name: 'breakdowns',
      component: () => import('@/views/BreakdownsView.vue'),
    },
    {
      path: '/sessions',
      name: 'sessions',
      component: () => import('@/views/SessionsView.vue'),
    },
    {
      path: '/machines',
      name: 'machines',
      component: () => import('@/views/MachinesView.vue'),
    },
    {
      path: '/report',
      name: 'report',
      component: () => import('@/views/ReportView.vue'),
    },
    {
      path: '/alerts',
      name: 'alerts',
      component: () => import('@/views/AlertsView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
    },
  ],
});
