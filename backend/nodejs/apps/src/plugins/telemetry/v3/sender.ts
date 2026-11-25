/**
 * Sends usage telemetry over contract v3, and decides each interval whether
 * v3 or the legacy endpoints carry the data.
 *
 * v3 in one paragraph: the install registers once and is given a secret; each
 * push is authenticated with it; metrics are sent as named points, counters as
 * the amount counted since the previous push; and every push has this
 * process's id and a sequence number, so the collector adds up the pushes of
 * several processes and applies a push it receives twice only once.
 *
 * Nothing is lost and nothing is counted twice:
 * - A batch is kept until the collector acknowledges it and is sent again
 *   unchanged, with the same number. There is one pending batch of metrics and
 *   one of events at most; what happens meanwhile stays in the registry and in
 *   the event buffer, both of which are bounded.
 * - A restarted process has a new process id and numbers from 1 again. The
 *   one batch the old process had not got acknowledged is lost with it.
 * - When an interval is delivered by the legacy endpoints instead (they send
 *   cumulative values), the counter baseline moves to that point, so a later
 *   switch to v3 does not send the same counts again.
 *
 * `USAGE_TELEMETRY_CONTRACT` selects the behaviour. `legacy` is the default
 * and never tries v3: v3 leaves out the organisation, the domain and the
 * operational metrics, and whether to give those up is for whoever runs the
 * install to decide. `auto` uses v3 and falls back to the legacy endpoints
 * when the collector is not a v3 collector, remembering that for a day before
 * asking again; `v3` never falls back.
 */
import { randomUUID } from 'crypto';
import type { BufferedEvent } from '../event-buffer';
import { DeltaTracker } from './delta-tracker';
import {
  MAX_POINTS_PER_BATCH,
  MetricSample,
  translateEvent,
  translateMetrics,
  V3Series,
} from './translate';

export type UsageContractMode = 'auto' | 'v3' | 'legacy';

export const USAGE_TELEMETRY_CONTRACT = 'USAGE_TELEMETRY_CONTRACT';
export const USAGE_TELEMETRY_EDITION = 'USAGE_TELEMETRY_EDITION';

export function usageContractMode(
  raw: string | undefined = process.env[USAGE_TELEMETRY_CONTRACT],
): UsageContractMode {
  const value = (raw ?? '').trim().toLowerCase();
  // v3 is opt-in: unset, empty or unrecognised means the legacy endpoints.
  return value === 'v3' || value === 'auto' ? value : 'legacy';
}

/** `ee` when the build says so, otherwise `oss`. */
export function usageEdition(
  raw: string | undefined = process.env[USAGE_TELEMETRY_EDITION],
): 'oss' | 'ee' {
  return (raw ?? '').trim().toLowerCase() === 'ee' ? 'ee' : 'oss';
}

/** What an HTTP answer looked like; `undefined` from `post` means there was none. */
export interface V3Response {
  status: number;
  /** The `X-Usage-Contract` header; `3` on every answer of a v3 collector. */
  contract?: string;
  retryAfterSeconds?: number;
  body?: unknown;
}

export interface V3HttpPort {
  post(
    url: string,
    body: unknown,
    headers: Record<string, string>,
  ): Promise<V3Response | undefined>;
}

/** Where the install's secret is kept: the encrypted configuration the install id lives in. */
export interface SecretStore {
  read(): Promise<string | undefined>;
  write(secret: string): Promise<void>;
}

export interface V3Identity {
  /** The configured collector URL (the legacy metrics endpoint). */
  serverUrl: string;
  installId: string;
  version: string;
  service: string;
}

export interface V3Logger {
  debug(message: string, meta?: unknown): void;
  warn(message: string, meta?: unknown): void;
}

export interface EventSource {
  /** Remove and return up to `max` events, oldest first. */
  take(max: number): BufferedEvent[];
  /** Put events back at the front, to be sent before newer ones. */
  putBack(events: BufferedEvent[]): void;
}

export interface UsageV3SenderOptions {
  mode: UsageContractMode;
  http: V3HttpPort;
  secrets: SecretStore;
  logger: V3Logger;
  snapshot: () => Promise<MetricSample[]> | MetricSample[];
  events: EventSource;
  now?: () => number;
  processId?: string;
  edition?: 'oss' | 'ee';
}

/** Which endpoints carry this interval. `none`: nothing is sent now; everything is kept. */
export type Route = 'v3' | 'legacy' | 'none';

export const CONTRACT_HEADER_VALUE = '3';
export const NOT_V3_TTL_MS = 24 * 60 * 60 * 1000;
export const BACKOFF_BASE_MS = 30 * 1000;
export const BACKOFF_MAX_MS = 30 * 60 * 1000;
export const MAX_EVENTS_PER_BATCH = 2000;
/** Credential refusals in a row before the install is treated as unable to use v3. */
export const MAX_REFUSALS = 3;

const VERSION = /^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$/;

type Kind = 'metrics' | 'events';
type Outcome = 'acked' | 'not_v3' | 'unauthorized' | 'rejected' | 'retry';

interface PendingMetrics {
  sequence: number;
  points: {
    name: string;
    type: string;
    labels: Record<string, string>;
    value: number;
  }[];
}

interface PendingEvents {
  sequence: number;
  /** As buffered, so they can still go to the legacy endpoint on a fallback. */
  raw: BufferedEvent[];
}

/** The v3 endpoint next to the configured legacy one: `.../collect-metrics` -> `.../v3/<name>`. */
export function v3Url(serverUrl: string, name: string): string {
  const url = new URL(serverUrl);
  const path = url.pathname
    .replace(/\/?collect-metrics\/?$/, '')
    .replace(/\/+$/, '');
  url.pathname = `${path}/v3/${name}`;
  url.search = '';
  url.hash = '';
  return url.toString();
}

export class UsageV3Sender {
  readonly processId: string;
  private readonly mode: UsageContractMode;
  private readonly http: V3HttpPort;
  private readonly secrets: SecretStore;
  private readonly logger: V3Logger;
  private readonly now: () => number;
  private readonly edition: 'oss' | 'ee';
  private readonly tracker = new DeltaTracker();
  private readonly sequence: Record<Kind, number> = { metrics: 0, events: 0 };

  private secret: string | undefined;
  /** A secret the collector issued that could not be stored yet. */
  private unsavedSecret: string | undefined;
  /** The secret the collector last refused, so it is not simply tried again. */
  private refusedSecret: string | undefined;
  private refusals = 0;
  private conflicts = 0;
  private notV3Until = 0;
  private retryAt = 0;
  private failures = 0;
  private pendingMetrics: PendingMetrics | undefined;
  private pendingEvents: PendingEvents | undefined;

  constructor(private readonly options: UsageV3SenderOptions) {
    this.mode = options.mode;
    this.http = options.http;
    this.secrets = options.secrets;
    this.logger = options.logger;
    this.now = options.now ?? Date.now;
    this.edition = options.edition ?? usageEdition();
    this.processId = options.processId ?? `node-${randomUUID()}`;
  }

  /**
   * Send what is due over v3 if v3 is to be used, and say which endpoints
   * carry this interval. On `legacy` the caller pushes the way it always has
   * and then calls `deliveredByLegacy`.
   */
  async tick(identity: V3Identity): Promise<Route> {
    if (this.mode === 'legacy') return 'legacy';
    if (this.mode === 'auto' && this.now() < this.notV3Until) return 'legacy';
    if (this.now() < this.retryAt) return 'none';

    const ready = await this.ensureSecret(identity);
    if (ready !== 'v3') return ready;

    const metrics = await this.sendMetrics(identity);
    if (metrics !== 'acked' && metrics !== 'rejected') {
      return this.afterFailure(metrics);
    }
    const events = await this.sendEvents(identity);
    if (events !== 'acked' && events !== 'rejected') {
      return this.afterFailure(events);
    }
    this.failures = 0;
    this.refusals = 0;
    this.refusedSecret = undefined;
    return 'v3';
  }

  /** The legacy endpoints delivered the registry as it is now: do not send those counts again. */
  async deliveredByLegacy(): Promise<void> {
    this.pendingMetrics = undefined;
    this.tracker.rebase(translateMetrics(await this.options.snapshot()));
  }

  /** For tests and diagnostics. Never includes the secret. */
  state(): {
    hasSecret: boolean;
    pendingMetrics: number | undefined;
    pendingEvents: number | undefined;
    notV3Until: number;
    retryAt: number;
  } {
    return {
      hasSecret: this.secret !== undefined,
      pendingMetrics: this.pendingMetrics?.sequence,
      pendingEvents: this.pendingEvents?.sequence,
      notV3Until: this.notV3Until,
      retryAt: this.retryAt,
    };
  }

  // -- credentials ----------------------------------------------------------

  private async ensureSecret(identity: V3Identity): Promise<Route> {
    if (this.unsavedSecret !== undefined)
      await this.saveSecret(this.unsavedSecret);
    if (this.secret !== undefined) return 'v3';

    // Another replica, or an earlier run of this one, may have registered already.
    const stored = await this.secrets.read();
    if (stored !== undefined && stored !== this.refusedSecret) {
      this.secret = stored;
      return 'v3';
    }
    if (stored !== undefined && this.refusals < MAX_REFUSALS) {
      // The collector refused this secret; give a rotation elsewhere a few
      // intervals to land before asking for a registration.
      this.refusals += 1;
      return 'none';
    }
    return this.register(identity);
  }

  private async register(identity: V3Identity): Promise<Route> {
    const response = await this.http.post(
      v3Url(identity.serverUrl, 'installs'),
      {
        installId: identity.installId,
        version: VERSION.test(identity.version) ? identity.version : '0.0.0',
        edition: this.edition,
      },
      {},
    );
    if (response === undefined) return this.afterFailure('retry');
    if (response.contract !== CONTRACT_HEADER_VALUE)
      return this.afterFailure('not_v3');

    const secret = (response.body as { secret?: unknown } | undefined)?.secret;
    if (
      (response.status === 201 || response.status === 200) &&
      typeof secret === 'string' &&
      secret !== ''
    ) {
      this.secret = secret;
      this.conflicts = 0;
      this.refusals = 0;
      this.refusedSecret = undefined;
      await this.saveSecret(secret);
      this.logger.debug('Registered this install for usage telemetry v3');
      return 'v3';
    }
    if (response.status === 409 || response.status === 401) {
      // The id is registered and its secret is not in our configuration.
      // Another replica may be about to store it, so look again a few times.
      this.conflicts += 1;
      if (this.conflicts < MAX_REFUSALS)
        return this.mode === 'auto' ? 'legacy' : 'none';
      this.conflicts = 0;
      this.refusals = 0;
      this.refusedSecret = undefined;
      this.logger.warn(
        'This install id is registered for usage telemetry v3 but its secret is not in the configuration; using the legacy endpoints for a day',
      );
      return this.afterFailure('not_v3');
    }
    if (response.status === 400) {
      this.logger.warn('The collector refused the registration request', {
        status: response.status,
      });
      return this.afterFailure('not_v3');
    }
    return this.afterFailure('retry', response.retryAfterSeconds);
  }

  private async saveSecret(secret: string): Promise<void> {
    try {
      await this.secrets.write(secret);
      this.unsavedSecret = undefined;
    } catch (error) {
      // Kept in memory and stored on a later interval; never logged.
      this.unsavedSecret = secret;
      this.logger.warn('Could not store the usage telemetry secret yet', {
        error: error instanceof Error ? error.message : 'unknown error',
      });
    }
  }

  // -- sending --------------------------------------------------------------

  private envelope(
    identity: V3Identity,
    sequence: number,
  ): Record<string, unknown> {
    return {
      processId: this.processId,
      sequence,
      service: identity.service,
      ...(VERSION.test(identity.version) ? { version: identity.version } : {}),
      edition: this.edition,
    };
  }

  private async sendMetrics(identity: V3Identity): Promise<Outcome> {
    if (this.pendingMetrics === undefined) {
      const series = this.tracker.cut(
        translateMetrics(await this.options.snapshot()),
      );
      if (series.length === 0) return 'acked';
      this.pendingMetrics = {
        sequence: (this.sequence.metrics += 1),
        points: this.points(series),
      };
    }
    const outcome = await this.post(identity, 'metrics', {
      ...this.envelope(identity, this.pendingMetrics.sequence),
      points: this.pendingMetrics.points,
    });
    if (outcome === 'acked' || outcome === 'rejected')
      this.pendingMetrics = undefined;
    return outcome;
  }

  private points(series: V3Series[]): PendingMetrics['points'] {
    // Counters first: a gauge left out is sent again next time, a delta is not.
    const ordered = [
      ...series.filter((s) => s.type === 'counter'),
      ...series.filter((s) => s.type !== 'counter'),
    ];
    if (ordered.length > MAX_POINTS_PER_BATCH) {
      this.logger.warn('Usage telemetry batch truncated', {
        points: ordered.length,
        kept: MAX_POINTS_PER_BATCH,
      });
    }
    return ordered.slice(0, MAX_POINTS_PER_BATCH).map((s) => ({
      name: s.name,
      type: s.type,
      labels: s.labels,
      value: s.value,
    }));
  }

  private async sendEvents(identity: V3Identity): Promise<Outcome> {
    if (this.pendingEvents === undefined) {
      const raw = this.options.events.take(MAX_EVENTS_PER_BATCH);
      if (raw.length === 0) return 'acked';
      this.pendingEvents = { sequence: (this.sequence.events += 1), raw };
    }
    const events = this.pendingEvents.raw
      .map(translateEvent)
      .filter((e): e is NonNullable<typeof e> => e !== undefined);
    if (events.length === 0) {
      // Nothing in this batch is a v3 event.
      this.pendingEvents = undefined;
      return 'acked';
    }
    const outcome = await this.post(identity, 'events', {
      ...this.envelope(identity, this.pendingEvents.sequence),
      events,
    });
    if (outcome === 'acked' || outcome === 'rejected')
      this.pendingEvents = undefined;
    return outcome;
  }

  private async post(
    identity: V3Identity,
    kind: Kind,
    body: unknown,
  ): Promise<Outcome> {
    const response = await this.http.post(
      v3Url(identity.serverUrl, kind),
      body,
      {
        Authorization: `Bearer ${identity.installId}.${this.secret ?? ''}`,
      },
    );
    if (response === undefined) return 'retry';
    if (response.contract !== CONTRACT_HEADER_VALUE) return 'not_v3';
    if (response.status === 200) return 'acked';
    if (response.status === 401) return 'unauthorized';
    if (response.status === 400 || response.status === 413) {
      // The same batch would be refused again, so it is given up.
      this.logger.warn(
        'The collector refused a usage telemetry batch; it is dropped',
        {
          kind,
          status: response.status,
        },
      );
      return 'rejected';
    }
    this.lastRetryAfter = response.retryAfterSeconds;
    return 'retry';
  }

  private lastRetryAfter: number | undefined;

  // -- failures -------------------------------------------------------------

  private afterFailure(outcome: Outcome, retryAfterSeconds?: number): Route {
    if (outcome === 'not_v3' && this.mode === 'auto') {
      this.notV3Until = this.now() + NOT_V3_TTL_MS;
      this.failures = 0;
      // The legacy push sends the registry as it is and the buffered events.
      this.pendingMetrics = undefined;
      if (this.pendingEvents !== undefined) {
        this.options.events.putBack(this.pendingEvents.raw);
        this.pendingEvents = undefined;
      }
      this.logger.debug(
        'The collector does not speak usage telemetry v3; using the legacy endpoints and asking again in a day',
      );
      return 'legacy';
    }
    if (outcome === 'unauthorized') {
      // The secret may have been rotated by another replica: read it again next time.
      this.refusedSecret = this.secret;
      this.secret = undefined;
      this.refusals += 1;
      return 'none';
    }
    // Also where `v3` mode lands when the collector is not a v3 collector:
    // it keeps everything and asks again, less and less often.
    this.failures += 1;
    const backoff = Math.min(
      BACKOFF_BASE_MS * 2 ** (this.failures - 1),
      BACKOFF_MAX_MS,
    );
    const asked = (retryAfterSeconds ?? this.lastRetryAfter ?? 0) * 1000;
    this.lastRetryAfter = undefined;
    this.retryAt = this.now() + Math.max(backoff, asked);
    return 'none';
  }
}
