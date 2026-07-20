// Hand-written ingest client over the generated wire types (Task 65.2, D-012,
// companion FR-TOK-011..016). Thin wrapper adding auth, batching, and a resilient
// submit policy: retry with exponential backoff + jitter on 429/5xx, pause on
// 401 (auth broken -- do not hammer), and poison-event isolation on 400/422 (a
// malformed event is set aside, by bisecting a rejected batch, so it never
// blocks the rest). The wire types come from ../openapi.json via
// `npm run generate`.

import type { components } from './generated';

/** A v2 usage event, exactly as the server's published schema defines it. */
export type UsageEventV2 = components['schemas']['UsageEventV2'];

/** Options for an {@link IngestClient}. */
export interface IngestClientOptions {
  /** Base URL of the tokemetry server. */
  serverUrl: string;
  /** Bearer token with the `ingest:events` scope. */
  token: string;
  /** Max events per batch (default 100). */
  batchSize?: number;
  /** Max uncompressed bytes per batch (default 256 KiB). */
  maxBatchBytes?: number;
  /** Max retry attempts for a 429/5xx batch (default 5). */
  maxRetries?: number;
  /** Base backoff in ms; attempt n waits ~base * 2^n plus jitter (default 200). */
  backoffBaseMs?: number;
  /** Injected fetch (defaults to global fetch). */
  fetchFn?: typeof fetch;
  /** Injected sleep (for tests). */
  sleepFn?: (ms: number) => Promise<void>;
  /** Injected [0,1) source for jitter (for tests). */
  randomFn?: () => number;
}

/** Outcome of an {@link IngestClient.ingest} call. */
export interface IngestResult {
  /** Events the server accepted. */
  accepted: number;
  /** Poison events isolated on 400/422. */
  rejected: number;
  /** Batches actually POSTed (including bisected sub-batches). */
  batches: number;
  /** The events the server rejected as malformed (400/422). */
  poisonEvents: UsageEventV2[];
}

/** Thrown when the server returns 401: the token is bad, so ingest pauses. */
export class IngestAuthError extends Error {
  constructor(message = 'ingest paused: authentication rejected (401)') {
    super(message);
    this.name = 'IngestAuthError';
  }
}

/** Thrown when a batch still fails after exhausting retries on 429/5xx. */
export class IngestRetryError extends Error {
  constructor(public readonly status: number) {
    super(`ingest failed after retries (last status ${status})`);
    this.name = 'IngestRetryError';
  }
}

const _DEFAULTS = {
  batchSize: 100,
  maxBatchBytes: 256 * 1024,
  maxRetries: 5,
  backoffBaseMs: 200,
};

export class IngestClient {
  private readonly opts: Required<
    Omit<IngestClientOptions, 'serverUrl' | 'token'>
  > &
    Pick<IngestClientOptions, 'serverUrl' | 'token'>;

  constructor(options: IngestClientOptions) {
    this.opts = {
      ..._DEFAULTS,
      fetchFn: (input, init) => fetch(input, init),
      sleepFn: (ms) => new Promise((r) => setTimeout(r, ms)),
      randomFn: () => Math.random(),
      ...options,
    };
  }

  /** Submit events, batching, retrying, and isolating poison events. */
  async ingest(events: UsageEventV2[]): Promise<IngestResult> {
    const result: IngestResult = {
      accepted: 0,
      rejected: 0,
      batches: 0,
      poisonEvents: [],
    };
    for (const batch of this.batches(events)) {
      await this.submit(batch, result);
    }
    return result;
  }

  /** Split events into batches bounded by count and serialized size. */
  private *batches(events: UsageEventV2[]): Generator<UsageEventV2[]> {
    let current: UsageEventV2[] = [];
    let bytes = 0;
    for (const event of events) {
      const size = JSON.stringify(event).length + 1;
      if (
        current.length > 0 &&
        (current.length >= this.opts.batchSize ||
          bytes + size > this.opts.maxBatchBytes)
      ) {
        yield current;
        current = [];
        bytes = 0;
      }
      current.push(event);
      bytes += size;
    }
    if (current.length > 0) yield current;
  }

  /** Submit one batch, bisecting on 400/422 to isolate poison events. */
  private async submit(
    batch: UsageEventV2[],
    result: IngestResult
  ): Promise<void> {
    const response = await this.post(batch);
    result.batches += 1;

    if (response.ok) {
      const body = (await this.readJson(response)) as { accepted?: number };
      result.accepted += body.accepted ?? batch.length;
      return;
    }
    if (response.status === 401) {
      throw new IngestAuthError();
    }
    if (response.status === 400 || response.status === 422) {
      if (batch.length === 1) {
        // A single event the server refuses is the poison; set it aside.
        result.rejected += 1;
        result.poisonEvents.push(batch[0]!);
        return;
      }
      // Bisect to find the offending event(s) without dropping the good ones.
      const mid = Math.floor(batch.length / 2);
      await this.submit(batch.slice(0, mid), result);
      await this.submit(batch.slice(mid), result);
      return;
    }
    // Non-retryable, non-poison status (shouldn't happen after post()'s retries).
    throw new IngestRetryError(response.status);
  }

  /** POST a batch with retry + backoff + jitter on 429/5xx. */
  private async post(batch: UsageEventV2[]): Promise<Response> {
    const url = `${this.opts.serverUrl}/api/v2/ingest/events`;
    const body = JSON.stringify({ schema_version: 2, events: batch });
    let last = 0;
    for (let attempt = 0; attempt <= this.opts.maxRetries; attempt += 1) {
      const response = await this.opts.fetchFn(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.opts.token}`,
          'Content-Type': 'application/json',
        },
        body,
      });
      if (response.status !== 429 && response.status < 500) {
        return response;
      }
      last = response.status;
      if (attempt < this.opts.maxRetries) {
        await this.opts.sleepFn(this.backoff(attempt));
      }
    }
    throw new IngestRetryError(last);
  }

  /** Full-jitter exponential backoff: random in [0, base * 2^attempt]. */
  private backoff(attempt: number): number {
    const ceiling = this.opts.backoffBaseMs * 2 ** attempt;
    return Math.floor(this.opts.randomFn() * ceiling);
  }

  private async readJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return {};
    }
  }
}
