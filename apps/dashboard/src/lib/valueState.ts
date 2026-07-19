// Provider-neutral value semantics for metric display (FR-DIM-010, FR-UI-012).
//
// A single number is not enough to render a metric honestly across providers:
// "0" (a real zero for the range), "no data" (unavailable), and "this provider
// does not offer this dimension" (unsupported, e.g. reasoning tokens for a
// provider without a reasoning phase) must read differently, never all collapse
// to a bare 0 or a dash. resolveValueState maps a raw value to that distinction;
// valueDisplay turns the state into display text plus a tooltip. Provenance
// (official reading vs estimated) is an orthogonal axis handled separately so a
// value of any state can still be marked estimated.
//
// Pure and unit-tested; the presentational LabeledValue component renders the
// result.

/** A metric value resolved into one of the four display states. */
export type ValueState =
  | { kind: 'value'; text: string }
  | { kind: 'zero'; text: string }
  | { kind: 'unavailable' }
  | { kind: 'unsupported' };

/** Whether a limit/metric reading is an official report or an estimate. */
export type Provenance = 'official' | 'estimated' | null;

export interface ResolveOptions {
  /** False when the provider does not offer this dimension at all. */
  supported?: boolean;
  /** Formatter for a present (non-null) numeric value; defaults to String. */
  format?: (value: number) => string;
}

/**
 * Resolve a numeric metric into a display state.
 *
 * Precedence: unsupported (provider lacks the dimension) beats unavailable
 * (no reading) beats zero (a real zero) beats a present value.
 */
export function resolveValueState(
  value: number | null | undefined,
  opts: ResolveOptions = {}
): ValueState {
  const { supported = true, format = String } = opts;
  if (!supported) return { kind: 'unsupported' };
  if (value === null || value === undefined) return { kind: 'unavailable' };
  if (value === 0) return { kind: 'zero', text: format(0) };
  return { kind: 'value', text: format(value) };
}

/**
 * Resolve a money metric (a nullable decimal string, the wire form) into a
 * display state. A non-numeric string is treated as unavailable.
 */
export function resolveMoneyState(
  value: string | null | undefined,
  opts: ResolveOptions = {}
): ValueState {
  if (!opts.supported && opts.supported !== undefined) {
    return { kind: 'unsupported' };
  }
  if (value === null || value === undefined) return { kind: 'unavailable' };
  const amount = Number(value);
  if (Number.isNaN(amount)) return { kind: 'unavailable' };
  return resolveValueState(amount, opts);
}

/** Display text, tooltip, and muted flag for a resolved value state. */
export interface ValueDisplay {
  text: string;
  title: string;
  /** True for the absent states (unavailable/unsupported) so the UI can dim them. */
  muted: boolean;
}

/** Turn a value state into display text plus an explanatory tooltip. */
export function valueDisplay(state: ValueState): ValueDisplay {
  switch (state.kind) {
    case 'value':
      return { text: state.text, title: '', muted: false };
    case 'zero':
      return { text: state.text, title: 'Zero over this range', muted: false };
    case 'unavailable':
      return { text: '—', title: 'No data available', muted: true };
    case 'unsupported':
      return {
        text: 'n/a',
        title: 'Not supported by this provider',
        muted: true,
      };
  }
}

/** A short badge word for provenance, or '' when unknown. */
export function provenanceLabel(provenance: Provenance): string {
  if (provenance === 'official') return 'official';
  if (provenance === 'estimated') return 'estimated';
  return '';
}
