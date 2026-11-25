/**
 * Reads a code sandbox load alert from the `notification` topic.
 *
 * The event carries no org and is not signed, so nothing in it is trusted as
 * text: only the type, the level and reason enums, a few safe identifiers and
 * bounded numbers are kept. Every sentence the admins read is built here from
 * fixed templates, and the link is a constant.
 */

/** Where the in-app notification and the email point. */
export const SANDBOX_ALERT_DOCS_URL =
  'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts';

export const SANDBOX_ALERT_ORIGIN = 'AI Service';

export const SANDBOX_ALERT_TYPES = {
  SANDBOX_CAPACITY_WARNING: 'warning',
  SANDBOX_CAPACITY_CRITICAL: 'critical',
  SANDBOX_CAPACITY_RESOLVED: 'resolved',
} as const;

export type SandboxAlertType = keyof typeof SANDBOX_ALERT_TYPES;

/** The accepted type names, as a set so a prototype name (`constructor`, `__proto__`) never matches. */
const SANDBOX_ALERT_TYPE_NAMES: ReadonlySet<string> = new Set(
  Object.keys(SANDBOX_ALERT_TYPES),
);

export const isSandboxAlertType = (value: unknown): value is SandboxAlertType =>
  typeof value === 'string' && SANDBOX_ALERT_TYPE_NAMES.has(value);
export type SandboxAlertKind = (typeof SANDBOX_ALERT_TYPES)[SandboxAlertType];
export type SandboxAlertLevel = 'warning' | 'critical';
export type SandboxAlertReason = 'memory' | 'cpu' | 'slots';

export const SANDBOX_ALERT_TITLES: Record<SandboxAlertKind, string> = {
  warning: 'Code runs are waiting for capacity',
  critical: 'Code runs are being refused',
  resolved: 'Code runs are no longer held back',
};

const SEVERITIES: Record<SandboxAlertKind, string> = {
  warning: 'warning',
  critical: 'critical',
  resolved: 'success',
};

const PAYLOAD_VERSION = 1;
/** Larger events are dropped before anything is read from them. */
export const MAX_EVENT_BYTES = 16 * 1024;
/** Used when the event has no readable expiry. */
export const DEFAULT_EXPIRY_MS = 6 * 3600_000;
/** No event is delivered later than this after it fired (or was received). */
export const MAX_EXPIRY_MS = 24 * 3600_000;
/** How far in the future `firedAt` may be, for clock skew. */
const MAX_FUTURE_SKEW_MS = 5 * 60_000;

const ALERT_ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const HOST_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$/;
const MODE_RE = /^[a-z0-9_]{1,32}$/;
const ISO_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;

/** The validated alert: only this is stored and rendered. */
export interface SandboxCapacityAlert {
  type: SandboxAlertType;
  kind: SandboxAlertKind;
  /** The level that fired, or for a resolved notice the level it resolves. */
  level: SandboxAlertLevel;
  alertId: string;
  /** False when the publisher says this level was already emailed. Never true for a resolved notice. */
  email: boolean;
  firedAt: string | null;
  /** Epoch ms after which the event is not delivered. */
  expiresAtMs: number;
  host: string | null;
  sandboxMode: string | null;
  memorySource: 'sidecar' | 'host';
  windowMinutes: number | null;
  runs: number | null;
  waited: number | null;
  refused: number | null;
  busyPercent: number | null;
  refusedPercent: number | null;
  longestWaitSeconds: number | null;
  slotWaitSeconds: number | null;
  emailCooldownSeconds: number | null;
  reason: SandboxAlertReason;
  reasonCounts: Record<SandboxAlertReason, number> | null;
  indexingSlowedSince: string | null;
  limit: {
    current: number | null;
    ceiling: number | null;
    floor: number | null;
  };
  hostMemoryTotalGb: number | null;
  hostMemoryFreeGb: number | null;
  resolvesAlertId: string | null;
  clearSince: string | null;
  alertFiredAt: string | null;
}

export type ParseResult =
  | { ok: true; alert: SandboxCapacityAlert }
  | { ok: false; reason: string; type?: string };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** *key* of *obj* when it is the object's own property; an inherited one is never read. */
const own = (obj: Record<string, unknown>, key: string): unknown =>
  Object.prototype.hasOwnProperty.call(obj, key) ? obj[key] : undefined;

const NO_FIELDS: Readonly<Record<string, unknown>> = Object.freeze(
  Object.create(null) as Record<string, unknown>,
);

/** The own fields of *value* in an object without a prototype, or null when it is not an object. */
function ownFields(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const fields: Record<string, unknown> = Object.create(null) as Record<
    string,
    unknown
  >;
  for (const key of Object.keys(value)) fields[key] = value[key];
  return fields;
}

/** A finite number in [low, high] (an integer when asked), else null. */
function bounded(
  value: unknown,
  low: number,
  high: number,
  integer = false,
): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if (value < low || value > high) return null;
  if (integer && !Number.isInteger(value)) return null;
  return value;
}

const count = (value: unknown): number | null =>
  bounded(value, 0, 100_000_000, true);

/** Whole percents, with one decimal below 10 %, as the publisher rounds them. */
function percent(value: unknown): number | null {
  const n = bounded(value, 0, 100);
  if (n === null) return null;
  return n >= 10 || Number.isInteger(n)
    ? Math.round(n)
    : Math.round(n * 10) / 10;
}

const gigabytes = (value: unknown): number | null => {
  const n = bounded(value, 0, 1_000_000);
  return n === null ? null : Math.round(n * 10) / 10;
};

const matching = (value: unknown, re: RegExp): string | null =>
  typeof value === 'string' && re.test(value) ? value : null;

function isoTime(value: unknown): string | null {
  const raw = matching(value, ISO_RE);
  return raw !== null && Number.isFinite(Date.parse(raw)) ? raw : null;
}

/** A short, safe label of an unexpected type for a log line. */
export function safeLabel(value: unknown): string {
  if (typeof value !== 'string') return typeof value;
  return value.replace(/[^A-Za-z0-9_.-]/g, '?').slice(0, 64);
}

function eventSize(raw: unknown): number {
  try {
    return JSON.stringify(raw).length;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

/**
 * Validates a `platformAdmins` event. Free text in it (`title`, `message`,
 * `redirectLink`, `reasonText`, `indexingText` and any other key) is ignored.
 */
export function parseSandboxCapacityAlert(
  raw: unknown,
  now: number = Date.now(),
): ParseResult {
  if (!isRecord(raw)) return { ok: false, reason: 'not_an_object' };
  if (eventSize(raw) > MAX_EVENT_BYTES)
    return { ok: false, reason: 'too_large' };
  const type = own(raw, 'type');
  if (!isSandboxAlertType(type)) {
    return { ok: false, reason: 'unknown_type', type: safeLabel(type) };
  }
  const alertType = type;
  const kind = SANDBOX_ALERT_TYPES[alertType];
  const p = ownFields(own(raw, 'payload'));
  if (!p || p.v !== PAYLOAD_VERSION) {
    return { ok: false, reason: 'bad_payload', type };
  }
  const alertId = matching(p.alertId, ALERT_ID_RE);
  if (!alertId) return { ok: false, reason: 'bad_alert_id', type };
  const level = p.level;
  if (level !== 'warning' && level !== 'critical') {
    return { ok: false, reason: 'bad_level', type };
  }
  if (kind !== 'resolved' && kind !== level) {
    return { ok: false, reason: 'level_does_not_match_type', type };
  }
  const reason =
    p.reason === undefined || p.reason === null ? 'slots' : p.reason;
  if (reason !== 'memory' && reason !== 'cpu' && reason !== 'slots') {
    return { ok: false, reason: 'bad_reason', type };
  }

  const firedAt = isoTime(p.firedAt);
  const firedAtMs = firedAt === null ? null : Date.parse(firedAt);
  if (firedAtMs !== null && firedAtMs > now + MAX_FUTURE_SKEW_MS) {
    return { ok: false, reason: 'fired_in_the_future', type };
  }
  const base = firedAtMs ?? now;
  const expiresAt = isoTime(p.expiresAt);
  const expiresAtMs = Math.min(
    expiresAt === null ? base + DEFAULT_EXPIRY_MS : Date.parse(expiresAt),
    base + MAX_EXPIRY_MS,
  );

  const limit = ownFields(p.limit) ?? NO_FIELDS;
  const counts = ownFields(p.reasonCounts);
  const reasonCounts = counts
    ? {
        slots: count(counts.slots) ?? 0,
        memory: count(counts.memory) ?? 0,
        cpu: count(counts.cpu) ?? 0,
      }
    : null;
  const alert: SandboxCapacityAlert = {
    type: alertType,
    kind,
    level,
    alertId,
    // Only an explicit `email: false` means in-app only; older events have no field.
    email: kind !== 'resolved' && own(raw, 'email') !== false,
    firedAt,
    expiresAtMs,
    host: matching(p.host, HOST_RE),
    sandboxMode: matching(p.sandboxMode, MODE_RE),
    memorySource: p.memorySource === 'sidecar' ? 'sidecar' : 'host',
    windowMinutes: bounded(p.windowMinutes, 1, 1440, true),
    runs: count(p.runs),
    waited: count(p.waited),
    refused: count(p.refused),
    busyPercent: percent(p.busyPercent),
    refusedPercent: percent(p.refusedPercent),
    longestWaitSeconds: bounded(p.longestWaitSeconds, 0, 604_800, true),
    slotWaitSeconds: bounded(p.slotWaitSeconds, 0, 86_400, true),
    emailCooldownSeconds: bounded(p.emailCooldownSeconds, 1, 604_800),
    reason,
    reasonCounts,
    indexingSlowedSince: isoTime(p.indexingSlowedSince),
    limit: {
      current: bounded(limit.current, 0, 100_000, true),
      ceiling: bounded(limit.ceiling, 0, 100_000, true),
      floor: bounded(limit.floor, 0, 100_000, true),
    },
    hostMemoryTotalGb: gigabytes(p.hostMemoryTotalGb),
    hostMemoryFreeGb: gigabytes(p.hostMemoryFreeGb),
    resolvesAlertId: matching(p.resolvesAlertId, ALERT_ID_RE),
    clearSince: isoTime(p.clearSince),
    alertFiredAt: isoTime(p.alertFiredAt),
  };
  return { ok: true, alert };
}

export const isExpired = (
  alert: SandboxCapacityAlert,
  now = Date.now(),
): boolean => now > alert.expiresAtMs;

/** "10:13 UTC". */
function hhmm(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

/** Why runs were held back, in one sentence. */
export function reasonSentence(alert: SandboxCapacityAlert): string {
  const sidecar = alert.memorySource === 'sidecar';
  if (alert.reason === 'memory') {
    return sidecar
      ? 'The code runner was short of memory, so new runs were held back.'
      : 'Free memory on the host was low, so new runs were held back to protect the databases and the app.';
  }
  if (alert.reason === 'cpu') {
    return `${sidecar ? "The code runner's" : "The host's"} CPU stayed busy, so fewer runs were allowed at once.`;
  }
  const ceiling = alert.limit.ceiling;
  const slots =
    ceiling === 1
      ? 'The only code-run slot was'
      : ceiling === null
        ? 'All code-run slots were'
        : `All ${ceiling} code-run slots were`;
  return `${slots} in use. The host still had free memory and CPU, so the slot limit was the bottleneck.`;
}

/**
 * What indexing already did, or null. Only for memory: CPU and slot waits do
 * not wait for indexing, and a sidecar does not share memory with indexing.
 */
export function indexingSentence(alert: SandboxCapacityAlert): string | null {
  if (
    alert.reason !== 'memory' ||
    alert.indexingSlowedSince === null ||
    alert.sandboxMode === 'helm_dind' ||
    alert.memorySource === 'sidecar'
  ) {
    return null;
  }
  return `Indexing was already slowed down from ${hhmm(alert.indexingSlowedSince)} to give chat priority.`;
}

const DEFAULT_WINDOW_MINUTES = 30;

function leadSentence(alert: SandboxCapacityAlert): string {
  const minutes = alert.windowMinutes ?? DEFAULT_WINDOW_MINUTES;
  if (alert.kind === 'critical') {
    const wait =
      alert.slotWaitSeconds === null
        ? ''
        : ` after waiting ${alert.slotWaitSeconds} s`;
    return (
      `${alert.refused ?? 0} of ${alert.runs ?? 0} code runs in the last ${minutes} minutes ` +
      `(${alert.refusedPercent ?? 0}%) were refused${wait} for a free sandbox.`
    );
  }
  return (
    `For ${alert.busyPercent ?? 0}% of the last ${minutes} minutes, code runs in chat waited for a free ` +
    `sandbox (${alert.waited ?? 0} runs waited).`
  );
}

function resolvedMessage(alert: SandboxCapacityAlert): string {
  const minutes = alert.windowMinutes ?? DEFAULT_WINDOW_MINUTES;
  const since = alert.clearSince ? `Since ${hhmm(alert.clearSince)}, c` : 'C';
  const where = alert.host ? ` on ${alert.host}` : '';
  const from = alert.alertFiredAt ? ` from ${hhmm(alert.alertFiredAt)}` : '';
  return (
    `${since}ode runs in chat${where} have had enough capacity for ${minutes} minutes. ` +
    `The ${alert.level} alert${from} is resolved.`
  );
}

/** The in-app text. */
export function alertMessage(alert: SandboxCapacityAlert): string {
  if (alert.kind === 'resolved') return resolvedMessage(alert);
  return [leadSentence(alert), reasonSentence(alert), indexingSentence(alert)]
    .filter((part): part is string => part !== null)
    .join(' ');
}

/** The fields stored on each admin's notification: fixed text, the constant link and the validated payload. */
export function storedAlertFields(alert: SandboxCapacityAlert): {
  type: string;
  severity: string;
  status: string;
  originService: string;
  title: string;
  message: string;
  redirectLink: string;
  payload: Record<string, unknown>;
} {
  const { type, kind, email: _email, expiresAtMs, ...rest } = alert;
  return {
    type,
    severity: SEVERITIES[kind],
    status: 'unread',
    originService: SANDBOX_ALERT_ORIGIN,
    title: SANDBOX_ALERT_TITLES[kind],
    message: alertMessage(alert),
    redirectLink: SANDBOX_ALERT_DOCS_URL,
    payload: {
      v: PAYLOAD_VERSION,
      ...rest,
      expiresAt: new Date(expiresAtMs).toISOString(),
    },
  };
}
