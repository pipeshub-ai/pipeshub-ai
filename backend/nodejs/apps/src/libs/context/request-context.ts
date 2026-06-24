import { AsyncLocalStorage } from 'async_hooks';
import { randomUUID } from 'crypto';

/**
 * Ambient request-id context (AsyncLocalStorage). Mirrors the Python side
 * (`app/utils/request_context.py`): same header name and envelope key.
 */

export interface RequestContextStore {
  rootId: string;
}

export const HEADER_REQUEST_ID = 'x-request-id';
export const ENVELOPE_REQUEST_ID = 'requestId';
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

/** Mint a root id for a system/automation-initiated unit of work. */
export function newSystemRoot(): string {
  return randomUUID();
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

/** Stamp the root id onto an outbound broker envelope (returns a copy). */
export function injectEnvelope<T extends Record<string, unknown>>(message: T): T {
  const ctx = storage.getStore();
  if (ctx && message[ENVELOPE_REQUEST_ID] === undefined) {
    return { ...message, [ENVELOPE_REQUEST_ID]: ctx.rootId };
  }
  return message;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
  const lower = name.toLowerCase();
  return Object.keys(headers).some((k) => k.toLowerCase() === lower);
}
