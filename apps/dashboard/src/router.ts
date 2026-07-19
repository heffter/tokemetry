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
      path: '/limits',
      name: 'limits',
      component: () => import('@/views/LimitsView.vue'),
    },
    {
      path: '/breakdowns',
      name: 'breakdowns',
      component: () => import('@/views/BreakdownsView.vue'),
    },
    {
      path: '/costs',
      name: 'costs',
      component: () => import('@/views/CostsView.vue'),
    },
    {
      path: '/requests',
      name: 'requests',
      component: () => import('@/views/RequestsView.vue'),
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
      path: '/sources',
      name: 'sources',
      component: () => import('@/views/SourcesView.vue'),
    },
    {
      path: '/data-quality',
      name: 'data-quality',
      component: () => import('@/views/DataQualityView.vue'),
    },
    {
      path: '/pricing-admin',
      name: 'pricing-admin',
      component: () => import('@/views/PricingAdminView.vue'),
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
