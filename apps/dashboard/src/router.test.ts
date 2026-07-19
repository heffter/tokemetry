import { describe, expect, it } from 'vitest';
import { router } from './router';

// Every path the app serves. The provider-neutral migration ADDS routes; it
// must never remove a pre-existing one (D-017).
const ALL_PATHS = [
  '/',
  '/trends',
  '/blocks',
  '/limits',
  '/breakdowns',
  '/costs',
  '/requests',
  '/sessions',
  '/machines',
  '/sources',
  '/data-quality',
  '/pricing-admin',
  '/report',
  '/alerts',
  '/settings',
];

// The routes that existed before the migration; these must keep resolving.
const V1_PATHS = [
  '/',
  '/trends',
  '/blocks',
  '/breakdowns',
  '/sessions',
  '/machines',
  '/report',
  '/alerts',
  '/settings',
];

describe('router', () => {
  const registered = router.getRoutes();
  const paths = registered.map((r) => r.path);

  it('registers every expected route', () => {
    for (const p of ALL_PATHS) expect(paths).toContain(p);
  });

  it('preserves every v1-era route (D-017: no route removed)', () => {
    for (const p of V1_PATHS) expect(paths).toContain(p);
  });

  it('gives every route a component', () => {
    for (const route of registered) {
      expect(route.components?.default).toBeDefined();
    }
  });

  it('has no duplicate paths', () => {
    expect(new Set(paths).size).toBe(paths.length);
  });
});
