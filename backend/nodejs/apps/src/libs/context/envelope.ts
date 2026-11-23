import {
  createHash,
  createHmac,
  hkdfSync,
  randomUUID,
  timingSafeEqual,
} from 'node:crypto';
import {
  OrgContext,
  spawnDetached,
  enforcing,
  makeOrgContext,
  orgContextStorage,
  runWithOrgContext,
  validateMode,
} from './orgContext';
import { AsyncLocalStorage } from 'node:async_hooks';
import { Logger } from '../services/logger.service';
import { metricsBackend } from '../services/telemetry/metrics-backend';
import {
  ENVELOPE_REQUEST_ID,
  sanitizeRootId,
  setContextIdentity,
} from './request-context';

/**
 * Signed broker envelope (v2); the Python twin is `app/core/envelope.py` and
 * both sign the same canonical string (a shared test vector pins this).
 *
 * Wire shape: `{ ctx: {v, eventId, orgId, userId, requestId, issuedAt, sig}, payload: "<JSON string>" }`.
 * `sig` is HMAC-SHA256 over the topic, the message `eventType`, the ctx fields and
 * the SHA-256 of the payload string exactly as transmitted.
 *
 * While `ENVELOPE_SIGN` is off, a message with no `ctx` falls back to `payload.orgId`,
 * logged and counted. While it is on, a message with no `ctx` is a stripped message:
 * counted, logged and (`ORG_CONTEXT_MODE=enforce`) rejected.
 *
 * Replay: a signature older than `ENVELOPE_MAX_AGE_SECONDS` (default 900) is stale
 * (counted and logged in shadow mode, rejected in enforce mode). A consumer given a group
 * name skips an event id its group already finished for that org (the durable inbox,
 * `platform.consumer_inbox`, see `libs/services/consumer-inbox.ts`); the id is recorded only
 * after the handler returned, so a failed handler is retried. Nothing keeps seen-ids in Redis:
 * an evicted id would let a replay through, and under `noeviction` the ids fill the server.
 */
export const DEFAULT_MAX_AGE_SECONDS = 900;
export const MAX_FUTURE_SKEW_SECONDS = 300;
export const DEFAULT_ADMITTED_MAX_AGE_SECONDS = 86400;

export class EnvelopeRejected extends Error {
  constructor(public readonly reason: string) {
    super(reason);
    this.name = 'EnvelopeRejected';
  }
}

export class DuplicateEnvelope extends Error {
  constructor(eventId: string) {
    super(eventId);
    this.name = 'DuplicateEnvelope';
  }
}

/** Which events each consumer group has finished, per org. */
export interface Inbox {
  /** True when `consumer` already finished this event. */
  seen(consumer: string, eventId: string, orgId: string): Promise<boolean>;
  /** Remember that `consumer` finished this event; called only after its handler succeeded. */
  record(consumer: string, eventId: string, orgId: string): Promise<void>;
  /**
   * Runs `fn` in one write unit of work with the record, so the effect `fn` writes there and the record commit
   * together. Throws DuplicateEnvelope (and rolls `fn` back) when another delivery of the event recorded it first.
   */
  recordWith<T>(
    consumer: string,
    eventId: string,
    orgId: string,
    fn: () => Promise<T>,
  ): Promise<T>;
}

/** pipeshub_consumer_inbox_errors_total{op}: inbox reads or writes that failed; the message ran as if new. */
const inboxErrors = metricsBackend.createCounter({
  name: 'pipeshub_consumer_inbox_errors_total',
  help: 'Consumer inbox reads or writes that failed; the message was processed as if new (at-least-once)',
  labelNames: ['op'],
});

/** pipeshub_consumer_inbox_skipped_total{consumer}: deliveries of an event the group had already finished. */
const inboxSkipped = metricsBackend.createCounter({
  name: 'pipeshub_consumer_inbox_skipped_total',
  help: 'Redelivered or replayed broker messages acknowledged without running the handler again',
  labelNames: ['consumer'],
});

/** pipeshub_envelope_unverified_total{topic}: ctx present but no key configured */
export const envelopeUnverified = new Map<string, number>();

/** pipeshub_envelope_ctx_missing_total{topic} */
export const envelopeCtxMissing = new Map<string, number>();

/** pipeshub_envelope_stale_total{topic}: validly signed but older than the accepted age */
export const envelopeStale = new Map<string, number>();

/** pipeshub_envelope_stripped_total{topic}: no ctx while signing is enabled */
export const envelopeStripped = new Map<string, number>();

/** pipeshub_envelope_unsigned_total{topic:reason}: sent unsigned while signing is enabled */
export const envelopeUnsigned = new Map<string, number>();

const bump = (m: Map<string, number>, k: string): void => {
  m.set(k, (m.get(k) ?? 0) + 1);
};

const lastLogged = new Map<string, number>();
/** Error log at most once per 30 s per key (the counters carry the exact count). */
function logLoud(
  key: string,
  msg: string,
  meta: Record<string, unknown>,
): void {
  const now = Date.now();
  if (now - (lastLogged.get(key) ?? 0) < 30_000) return;
  lastLogged.set(key, now);
  Logger.getInstance().error(msg, { component: 'Envelope', ...meta });
}

export function signEnabled(): boolean {
  return ['1', 'true', 'yes'].includes(
    (process.env.ENVELOPE_SIGN ?? 'false').toLowerCase(),
  );
}

function positiveSeconds(name: string, fallback: number): number {
  const raw = (process.env[name] ?? '').trim();
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number, got '${raw}'`);
  }
  return value;
}

export function maxAgeSeconds(): number {
  return positiveSeconds('ENVELOPE_MAX_AGE_SECONDS', DEFAULT_MAX_AGE_SECONDS);
}

/**
 * How long the inbox keeps an event id: the same window as the Python twin (`replay_window_seconds`), the longest a copy of a
 * signed envelope passes the age check there (its age or a stored PostgreSQL-queue admission, plus the future skew). Both
 * languages prune the one table, so the windows must match.
 */
export function replayWindowSeconds(): number {
  return (
    Math.max(
      maxAgeSeconds(),
      positiveSeconds(
        'ENVELOPE_ADMITTED_MAX_AGE_SECONDS',
        DEFAULT_ADMITTED_MAX_AGE_SECONDS,
      ),
    ) + MAX_FUTURE_SKEW_SECONDS
  );
}

export function ctxRequired(): boolean {
  return ['1', 'true', 'yes'].includes(
    (process.env.ENVELOPE_CTX_REQUIRED ?? 'false').toLowerCase(),
  );
}

/** A consumer writing org-scoped tables never runs with the unsigned fallback. */
export function assertMayStart(writesOrgScopedTables: boolean): void {
  if (writesOrgScopedTables && !ctxRequired()) {
    throw new Error(
      'ENVELOPE_CTX_REQUIRED must be true for consumers that write org-scoped tables',
    );
  }
}

/** HKDF-SHA256(secret, salt 'pipeshub', info 'envelope-ctx:v2'), 32 bytes. */
export function deriveKey(scopedJwtSecret: string): Buffer {
  return Buffer.from(
    hkdfSync('sha256', scopedJwtSecret, 'pipeshub', 'envelope-ctx:v2', 32),
  );
}

export interface EnvelopeCtx {
  v: 2;
  eventId: string;
  orgId: string;
  userId: string | null;
  requestId: string | null;
  issuedAt: number;
  sig?: string;
}

export function computeSig(
  key: Buffer,
  topic: string,
  c: Omit<EnvelopeCtx, 'sig' | 'v'>,
  payload: string,
  eventType = '',
): string {
  const canon = [
    'v2',
    topic,
    eventType,
    c.eventId,
    c.orgId,
    c.userId ?? '',
    c.requestId ?? '',
    String(c.issuedAt),
    createHash('sha256').update(payload).digest('hex'),
  ].join('\n');
  return createHmac('sha256', key).update(canon).digest('base64url');
}

/** Producer side: serialise the payload once and sign that exact string. */
export function sign(
  key: Buffer,
  topic: string,
  payload: Record<string, unknown>,
  ctx: OrgContext,
  issuedAt?: number,
  eventType = '',
): { eventType?: string; ctx: EnvelopeCtx; payload: string } {
  if (ctx.orgId === null) throw new Error('cannot sign a system context');
  const body = JSON.stringify(payload);
  const c: EnvelopeCtx = {
    v: 2,
    eventId: randomUUID(),
    orgId: ctx.orgId,
    userId: ctx.userId,
    requestId: ctx.requestId,
    issuedAt: issuedAt ?? Math.floor(Date.now() / 1000),
  };
  c.sig = computeSig(key, topic, c, body, eventType);
  return eventType
    ? { eventType, ctx: c, payload: body }
    : { ctx: c, payload: body };
}

export interface Opened {
  /** null: unsigned message with no payload.orgId (stays unbound, fails closed) */
  ctx: OrgContext | null;
  /** `ctx` removed, `payload` decoded */
  message: Record<string, unknown>;
  /** The signed `ctx.eventId`, else the producer's top-level `eventId`; absent from an older producer's message. */
  eventId?: string;
}

export function legacyCtx(message: Record<string, unknown>): OrgContext | null {
  const payload = message.payload as Record<string, unknown> | undefined;
  const orgId =
    payload && typeof payload === 'object' ? payload.orgId : undefined;
  if (typeof orgId !== 'string' || !orgId) return null;
  return makeOrgContext({
    orgId,
    userId: null,
    kind: 'service',
    source: 'legacy-payload',
    requestId:
      sanitizeRootId(message[ENVELOPE_REQUEST_ID] as string | undefined) ?? '-',
  });
}

/** Unsigned message in the v1 shape (a v2 payload string is decoded, else the message is malformed). */
function unsigned(message: Record<string, unknown>): Opened {
  const { ctx: _c, org_ctx: _o, ...rest } = message;
  let payload = rest.payload;
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      throw new EnvelopeRejected('malformed');
    }
    if (
      payload === null ||
      typeof payload !== 'object' ||
      Array.isArray(payload)
    )
      throw new EnvelopeRejected('malformed');
  }
  const flat = 'payload' in rest ? { ...rest, payload } : rest;
  return {
    ctx: legacyCtx(flat),
    message: flat,
    ...(isStr(message.eventId) && { eventId: message.eventId }),
  };
}

const isStr = (v: unknown): v is string =>
  typeof v === 'string' && v.length > 0;

function sigOk(
  key: Buffer,
  topic: string,
  c: EnvelopeCtx,
  payload: string,
  eventType: string,
): boolean {
  const want = Buffer.from(computeSig(key, topic, c, payload, eventType));
  const got = Buffer.from(String(c.sig));
  return want.length === got.length && timingSafeEqual(want, got);
}

/** Verify a consumed message and derive its OrgContext. Rejects with EnvelopeRejected. */
// eslint-disable-next-line @typescript-eslint/require-await -- async so that a refusal is a rejected promise, never a synchronous throw
export async function openEnvelope(
  topic: string,
  message: Record<string, unknown>,
  opts: {
    keys?: Buffer[];
    required?: boolean;
    now?: () => number;
  } = {},
): Promise<Opened> {
  const required = opts.required ?? ctxRequired();
  const c = message.ctx as EnvelopeCtx | undefined | null;
  if (c === undefined || c === null) {
    if (required) throw new EnvelopeRejected('ctx_missing');
    if (signEnabled()) {
      // Producers sign, so a message without ctx was stripped or came from an unsigned writer.
      bump(envelopeStripped, topic);
      if (enforcing()) throw new EnvelopeRejected('ctx_stripped');
      logLoud(
        `stripped:${topic}`,
        'envelope ctx missing while signing is enabled: accepted in shadow mode',
        { topic },
      );
    } else {
      bump(envelopeCtxMissing, topic);
    }
    return unsigned(message);
  }
  const payload = message.payload;
  if ((opts.keys ?? []).length === 0) {
    // No key available. Verification configured (signing or required on): never silently skip it.
    if (required || (signEnabled() && enforcing()))
      throw new EnvelopeRejected('no_key');
    bump(envelopeUnverified, topic);
    if (signEnabled()) {
      logLoud(
        `nokey:${topic}`,
        'envelope key unavailable: processing unverified (shadow mode)',
        { topic },
      );
    } else {
      Logger.getInstance().warn(
        'envelope ctx present but no key configured: processing unverified',
        { component: 'Envelope', topic },
      );
    }
    return unsigned(message);
  }
  if (
    typeof c !== 'object' ||
    (c.v as number) !== 2 ||
    typeof payload !== 'string' ||
    !isStr(c.eventId) ||
    !isStr(c.orgId) ||
    !isStr(c.sig) ||
    !Number.isInteger(c.issuedAt)
  ) {
    throw new EnvelopeRejected('malformed');
  }
  const eventType =
    typeof message.eventType === 'string' ? message.eventType : '';
  if (!(opts.keys ?? []).some((k) => sigOk(k, topic, c, payload, eventType))) {
    throw new EnvelopeRejected('bad_signature');
  }
  const age = (opts.now ? opts.now() : Date.now() / 1000) - c.issuedAt;
  if (age > maxAgeSeconds()) {
    bump(envelopeStale, topic);
    if (enforcing()) throw new EnvelopeRejected('stale');
    logLoud(
      `stale:${topic}`,
      'validly signed envelope older than the accepted age: accepted in shadow mode',
      {
        topic,
        maxAgeSeconds: maxAgeSeconds(),
      },
    );
  }
  if (-age > MAX_FUTURE_SKEW_SECONDS) throw new EnvelopeRejected('future');
  let body: unknown;
  try {
    body = JSON.parse(payload);
  } catch {
    throw new EnvelopeRejected('malformed');
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body))
    throw new EnvelopeRejected('malformed');
  const bodyOrg = (body as Record<string, unknown>).orgId;
  if (bodyOrg !== undefined && bodyOrg !== null && bodyOrg !== c.orgId)
    throw new EnvelopeRejected('org_mismatch');
  const { ctx: _ctx, org_ctx: _org, ...rest } = message;
  return {
    ctx: makeOrgContext({
      orgId: c.orgId,
      userId: c.userId ?? null,
      kind: c.userId ? 'user' : 'service',
      source: 'envelope',
      requestId: sanitizeRootId(c.requestId ?? undefined) ?? '-',
    }),
    message: { ...rest, payload: body },
    eventId: c.eventId,
  };
}

let keyProvider: (() => Promise<Buffer[]>) | null = null;
let signKey: Buffer | null = null;
let inbox: Inbox | null = null;

/** Startup wiring: the durable consumer inbox, or null for none (consumers then keep the broker's at-least-once delivery). */
export function configureInbox(impl: Inbox | null): void {
  inbox = impl;
}

/** Startup wiring: key provider (current key first, previous key during rotation) and the consumer inbox. */
export function configureEnvelope(
  keys: (() => Promise<Buffer[]>) | null,
  inboxImpl: Inbox | null = null,
  producerKey: Buffer | null = null,
): void {
  keyProvider = keys;
  inbox = inboxImpl;
  signKey = producerKey;
}

let keyRetry: NodeJS.Timeout | null = null;

/**
 * Startup wiring from `scopedJwtSecret` (no new secret). Throws for an invalid `ORG_CONTEXT_MODE` /
 * `ENVELOPE_MAX_AGE_SECONDS`. Without a secret it logs an error and, when `reload` is given, retries
 * it in the background (2 s doubling to 60 s) until it loads, so verification is never off for the
 * process lifetime. While there is no key, enforce mode rejects what needs verification and shadow mode
 * counts and logs it.
 */
export function configureEnvelopeFromSecret(
  scopedJwtSecret: string | undefined,
  reload?: () => Promise<string | undefined>,
): void {
  validateMode();
  replayWindowSeconds();
  if (keyRetry) clearTimeout(keyRetry);
  keyRetry = null;
  if (scopedJwtSecret) {
    const key = deriveKey(scopedJwtSecret);
    return configureEnvelope(() => Promise.resolve([key]), inbox, key);
  }
  Logger.getInstance().error(
    'envelope: scopedJwtSecret is missing; signing and verification are degraded',
    { component: 'Envelope' },
  );
  configureEnvelope(null, inbox);
  if (!reload) return;
  let delayMs = keyRetryInitialMs;
  const attempt = (): void => {
    keyRetry = spawnDetached(() =>
      setTimeout(() => {
        reload()
          .then((secret) => {
            if (!secret) throw new Error('scopedJwtSecret is missing');
            keyRetry = null;
            const key = deriveKey(secret);
            configureEnvelope(() => Promise.resolve([key]), inbox, key);
            Logger.getInstance().warn('envelope: key loaded after retry', {
              component: 'Envelope',
            });
          })
          .catch(() => {
            delayMs = Math.min(delayMs * 2, 60_000);
            attempt();
          });
      }, delayMs),
    );
    keyRetry.unref();
  };
  attempt();
}

/** Initial background key retry delay (ms); tests shorten it. */
export let keyRetryInitialMs = 2000;
export function setKeyRetryInitialMs(ms: number): void {
  keyRetryInitialMs = ms;
}

/** The message with a top-level `eventId` (a UUID) unless it already names one. */
export function withEventId<T extends Record<string, unknown>>(
  message: T,
): T | (T & { eventId: string }) {
  const existing = message.eventId;
  if (typeof existing === 'string' && existing.length > 0) return message;
  return { ...message, eventId: randomUUID() };
}

/**
 * Producer hook: sign when `ENVELOPE_SIGN` is on, and give every message an event id (a UUID at publish).
 * Signing adds integrity, not identity: a signed message carries the id in `ctx.eventId`, any other leaves with a
 * top-level `eventId`, so consumers can tell a redelivery of an event from a new event.
 * Signed shape: top-level fields stay, `payload` becomes the signed JSON string, `ctx` is added.
 * With the flag off the message is sent as v1, plus `eventId`. With it on, a message that cannot be signed
 * (no org bound, payload naming another org) is counted in `envelopeUnsigned` and sent unsigned (with `eventId`);
 * with no key it throws in enforce mode and is counted and logged in shadow mode.
 */
export function signMessage<T extends Record<string, unknown>>(
  topic: string,
  message: T,
): T | Record<string, unknown> {
  if (!signEnabled()) return withEventId(message);
  const ctx = orgContextStorage.getStore();
  const payload = message.payload;
  if (
    !ctx ||
    ctx.orgId === null ||
    payload === null ||
    typeof payload !== 'object' ||
    Array.isArray(payload)
  ) {
    bump(envelopeUnsigned, `${topic}:no_org`);
    return withEventId(message);
  }
  const bodyOrg = (payload as Record<string, unknown>).orgId;
  if (bodyOrg !== undefined && bodyOrg !== null && bodyOrg !== ctx.orgId) {
    bump(envelopeUnsigned, `${topic}:org_mismatch`);
    return withEventId(message);
  }
  if (!signKey) {
    bump(envelopeUnsigned, `${topic}:no_key`);
    if (enforcing())
      throw new Error(
        `envelope signing is enabled but no key is available (topic=${topic})`,
      );
    logLoud(
      `signkey:${topic}`,
      'envelope signing enabled but no key is available: sent unsigned (shadow mode)',
      { topic },
    );
    return withEventId(message);
  }
  const { payload: _p, ctx: _c, ...rest } = message;
  const eventType =
    typeof message.eventType === 'string' ? message.eventType : '';
  return {
    ...rest,
    ...sign(
      signKey,
      topic,
      payload as Record<string, unknown>,
      ctx,
      undefined,
      eventType,
    ),
  };
}

export async function openConfigured(
  topic: string,
  message: Record<string, unknown>,
): Promise<Opened> {
  return openEnvelope(topic, message, {
    keys: keyProvider ? await keyProvider() : [],
  });
}

/** One delivery a consumer group handles: what the inbox is asked about and records. */
interface Delivery {
  inbox: Inbox;
  consumer: string;
  eventId: string;
  /** Unset for a message with no org of its own (one that fans out to several orgs): only `withDeliveryRecorded(fn, org)` records it. */
  orgId: string | undefined;
  recorded: boolean;
}

const deliveryStorage = new AsyncLocalStorage<Delivery>();

/** Counts and logs (at most every 30 s) an inbox call that failed; the message runs as if new. */
export function inboxFailed(op: string, consumer: string, e: unknown): void {
  inboxErrors.inc({ op });
  logLoud(
    `inbox-${op}:${consumer}`,
    'consumer inbox call failed: the message is processed as if new',
    { op, consumer, error: e instanceof Error ? e.name : typeof e },
  );
}

function skipped(d: Delivery, topic: string): void {
  inboxSkipped.inc({ consumer: d.consumer });
  Logger.getInstance().info(
    'broker message already processed by this consumer group: acknowledged without running it again',
    { component: 'Envelope', topic, consumer: d.consumer, eventId: d.eventId },
  );
}

/**
 * Only a message naming its event id can be matched, and only per org: the signed org, else the one the handler acts on
 * (`payload.orgId`, or a flat message's `orgId`). A message without an org is matched only per org it fans out to.
 */
function deliveryOf(
  consumer: string,
  opened: Opened,
  value: Record<string, unknown>,
): Delivery | undefined {
  const orgId = opened.ctx?.orgId ?? value.orgId;
  if (!inbox || !consumer || !opened.eventId) return undefined;
  return {
    inbox,
    consumer,
    eventId: opened.eventId,
    orgId: isStr(orgId) ? orgId : undefined,
    recorded: false,
  };
}

/**
 * Consumer wrapper: verify, bind the org for `fn` only, and with a `consumer` (the group) and an inbox skip an event the
 * group already finished; the event is recorded after `fn` returned. Throws EnvelopeRejected (caller dead-letters/acks);
 * a duplicate returns undefined and is acknowledged by the caller like a handled message.
 */
export async function runWithEnvelope<V, R>(
  topic: string,
  value: V,
  fn: (value: V) => Promise<R>,
  consumer = '',
): Promise<R | undefined> {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    return fn(value);
  const opened = await openConfigured(topic, value as Record<string, unknown>);
  const d = deliveryOf(consumer, opened, value as Record<string, unknown>);
  if (d?.orgId) {
    let seen = false;
    try {
      seen = await d.inbox.seen(d.consumer, d.eventId, d.orgId);
    } catch (e) {
      inboxFailed('seen', d.consumer, e);
    }
    if (seen) {
      skipped(d, topic);
      return undefined;
    }
  }
  const run = async (): Promise<R | undefined> => {
    if (!d) return fn(opened.message as V);
    let out: R;
    try {
      out = await deliveryStorage.run(d, () => fn(opened.message as V));
    } catch (e) {
      if (!(e instanceof DuplicateEnvelope)) throw e;
      skipped(d, topic);
      return undefined;
    }
    if (d.orgId && !d.recorded) {
      try {
        await d.inbox.record(d.consumer, d.eventId, d.orgId);
      } catch (e) {
        inboxFailed('record', d.consumer, e);
      }
    }
    return out;
  };
  if (!opened.ctx) return run();
  // Only a verified signature replaces the unverified org the consumer's log context was opened with.
  if (opened.ctx.source === 'envelope') setContextIdentity(opened.ctx);
  return runWithOrgContext(opened.ctx, run);
}

/**
 * For a handler whose effect is one PostgreSQL unit of work: runs `fn` in a unit that also records the delivery for `orgId`
 * (default: the message's org), so the effect and the record commit together. It throws DuplicateEnvelope, rolling `fn` back,
 * when the event was already recorded for that org: by a second delivery running at the same time, or, for a message that fans
 * out to several orgs, by an earlier delivery that reached this org. Outside a delivery with an inbox it just runs `fn`.
 */
export async function withDeliveryRecorded<T>(
  fn: () => Promise<T>,
  orgId?: string,
): Promise<T> {
  const d = deliveryStorage.getStore();
  const org = orgId ?? d?.orgId;
  if (!d || !org) return fn();
  if (org !== d.orgId)
    return d.inbox.recordWith(d.consumer, d.eventId, org, fn);
  if (d.recorded) return fn();
  d.recorded = true;
  return d.inbox.recordWith(d.consumer, d.eventId, org, fn);
}
