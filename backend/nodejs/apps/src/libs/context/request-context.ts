import { AsyncLocalStorage } from 'async_hooks';
import { randomUUID } from 'crypto';

/**
 * Ambient request-id context (AsyncLocalStorage). Mirrors the Python side
 * (`app/utils/request_context.py`): same header name and envelope key.
 */

export interface RequestContextStore {
  rootId: string;
  /**
   * Set by the auth layer once the JWT is decoded (context middleware runs
   * before auth, so org is unknown at store-creation time). Never written to a
   * log record raw: the logging provider decides how, or whether, to show it.
   */
  orgId?: string;
}

export const HEADER_REQUEST_ID = 'x-request-id';
export const ENVELOPE_REQUEST_ID = 'requestId';
export const ENVELOPE_ORG_ID = 'orgId';
export const NO_CONTEXT = '-';

// `<objectId:24>-<nanoid:21>`
const MAX_ROOT_ID_LEN = 64;
const UNSAFE_ID_CHARS = /[^A-Za-z0-9._:-]/g;

/** Strip a client-supplied id to a safe charset + length. `undefined` if empty. */
export function sanitizeRootId(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  const cleaned = raw.replace(UNSAFE_ID_CHARS, '').slice(0, MAX_ROOT_ID_LEN);
  return cleaned || undefined;
}

const storage = new AsyncLocalStorage<RequestContextStore>();

/** Run `fn` with the given context bound for its entire async subtree. */
export function runWithRequestContext<T>(
  ctx: RequestContextStore,
  fn: () => T,
): T {
  return storage.run(ctx, fn);
}

export function getRequestContext(): RequestContextStore | undefined {
  return storage.getStore();
}

export function getRootId(): string | undefined {
  return storage.getStore()?.rootId;
}

/**
 * Attach the org to the in-flight context (no-op without a context). Called by
 * the auth layer after JWT decode; mutates the current store in place so the
 * ambient context the logging layer reads picks it up without re-binding.
 */
export function setContextOrgId(orgId: unknown): void {
  const ctx = storage.getStore();
  if (ctx && typeof orgId === 'string' && orgId) {
    ctx.orgId = sanitizeRootId(orgId);
  }
}

export function getOrgId(): string | undefined {
  return storage.getStore()?.orgId;
}

/**
 * The `sys-` prefix marks it as machine-originated work.
 */
export function newSystemRoot(): string {
  return `sys-${randomUUID().replace(/-/g, '')}`;
}

/** Fallback root id when an inbound request carries none (mirrors Python `new_anon_root`). */
export function newAnonRoot(): string {
  return `anon-${randomUUID().replace(/-/g, '')}`;
}

/** The id stamped into log lines: `<root><service-suffix>` (node suffix is empty). */
export function currentDisplayId(suffix = ''): string {
  const ctx = storage.getStore();
  if (!ctx) return NO_CONTEXT;
  return `${ctx.rootId}${suffix}`;
}

/** Add the root id to outbound HTTP headers (returns a copy, never mutates). */
export function injectRequestHeaders(
  headers: Record<string, string> = {},
): Record<string, string> {
  const out = { ...headers };
  const ctx = storage.getStore();
  if (ctx && !hasHeader(out, HEADER_REQUEST_ID)) {
    out[HEADER_REQUEST_ID] = ctx.rootId;
  }
  return out;
}

/** Stamp root id and org onto an outbound broker envelope (returns a copy). */
export function injectEnvelope<T extends Record<string, unknown>>(message: T): T {
  const ctx = storage.getStore();
  if (!ctx) return message;
  const out: Record<string, unknown> = { ...message };
  if (out[ENVELOPE_REQUEST_ID] === undefined) {
    out[ENVELOPE_REQUEST_ID] = ctx.rootId;
  }
  if (ctx.orgId && out[ENVELOPE_ORG_ID] === undefined) {
    out[ENVELOPE_ORG_ID] = ctx.orgId;
  }
  return out as T;
}

/**
 * Build a consumer-side context from a broker envelope (mirrors Python
 * `context_from_envelope`). A message with no id gets a root from `newRoot`,
 * by default an `anon-` one — a propagation gap, not self-initiated machine work.
 */
export function contextFromEnvelope(
  value: unknown,
  newRoot: () => string = newAnonRoot,
): RequestContextStore {
  const env = (value ?? {}) as Record<string, unknown>;
  const orgId = sanitizeRootId(envelopeString(env, ENVELOPE_ORG_ID));
  return {
    rootId:
      sanitizeRootId(envelopeString(env, ENVELOPE_REQUEST_ID)) ?? newRoot(),
    ...(orgId ? { orgId } : {}),
  };
}

/** An envelope field is producer-supplied: anything but a string is ignored. */
function envelopeString(
  env: Record<string, unknown>,
  key: string,
): string | undefined {
  const raw = env[key];
  return typeof raw === 'string' ? raw : undefined;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
  const lower = name.toLowerCase();
  return Object.keys(headers).some((k) => k.toLowerCase() === lower);
}
