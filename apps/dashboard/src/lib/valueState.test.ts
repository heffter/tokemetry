import { describe, expect, it } from 'vitest';
import {
  provenanceLabel,
  resolveMoneyState,
  resolveValueState,
  valueDisplay,
} from './valueState';

describe('resolveValueState', () => {
  it('marks a dimension the provider does not offer as unsupported', () => {
    expect(resolveValueState(0, { supported: false })).toEqual({
      kind: 'unsupported',
    });
    // Unsupported wins even over a present value.
    expect(resolveValueState(42, { supported: false })).toEqual({
      kind: 'unsupported',
    });
  });

  it('marks a missing reading as unavailable', () => {
    expect(resolveValueState(null)).toEqual({ kind: 'unavailable' });
    expect(resolveValueState(undefined)).toEqual({ kind: 'unavailable' });
  });

  it('distinguishes a real zero from unavailable', () => {
    expect(resolveValueState(0)).toEqual({ kind: 'zero', text: '0' });
  });

  it('formats a present value with the supplied formatter', () => {
    expect(resolveValueState(1500, { format: (n) => `${n / 1000}K` })).toEqual({
      kind: 'value',
      text: '1.5K',
    });
  });
});

describe('resolveMoneyState', () => {
  it('parses a decimal string and distinguishes zero from unavailable', () => {
    expect(resolveMoneyState(null)).toEqual({ kind: 'unavailable' });
    expect(
      resolveMoneyState('0', { format: (n) => `$${n.toFixed(2)}` })
    ).toEqual({ kind: 'zero', text: '$0.00' });
    expect(
      resolveMoneyState('1.5', { format: (n) => `$${n.toFixed(2)}` })
    ).toEqual({ kind: 'value', text: '$1.50' });
  });

  it('treats a non-numeric string as unavailable', () => {
    expect(resolveMoneyState('n/a')).toEqual({ kind: 'unavailable' });
  });

  it('honors an explicit unsupported flag', () => {
    expect(resolveMoneyState('1.0', { supported: false })).toEqual({
      kind: 'unsupported',
    });
  });
});

describe('valueDisplay', () => {
  it('renders each state with distinct text and muting', () => {
    expect(valueDisplay({ kind: 'value', text: '5.0M' })).toEqual({
      text: '5.0M',
      title: '',
      muted: false,
    });
    expect(valueDisplay({ kind: 'zero', text: '0' }).muted).toBe(false);
    expect(valueDisplay({ kind: 'unavailable' })).toEqual({
      text: '—',
      title: 'No data available',
      muted: true,
    });
    expect(valueDisplay({ kind: 'unsupported' }).text).toBe('n/a');
    expect(valueDisplay({ kind: 'unsupported' }).muted).toBe(true);
  });
});

describe('provenanceLabel', () => {
  it('labels official and estimated, and is empty when unknown', () => {
    expect(provenanceLabel('official')).toBe('official');
    expect(provenanceLabel('estimated')).toBe('estimated');
    expect(provenanceLabel(null)).toBe('');
  });
});
