import { describe, expect, it } from 'vitest';
import { deepLinkFor } from './dataQuality';

describe('deepLinkFor', () => {
  it('routes unknown provider/model events to pricing admin', () => {
    expect(deepLinkFor('unknown_model')?.to).toBe('/pricing-admin');
    expect(deepLinkFor('unknown_provider')?.to).toBe('/pricing-admin');
  });

  it('routes pricing-coverage events to the cost view', () => {
    expect(deepLinkFor('unpriced_event')?.to).toBe('/costs');
    expect(deepLinkFor('partial_price')?.to).toBe('/costs');
  });

  it('has no deep link for non-actionable kinds', () => {
    expect(deepLinkFor('clock_skew')).toBeNull();
    expect(deepLinkFor('sequence_conflict')).toBeNull();
  });
});
