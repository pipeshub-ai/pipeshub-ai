import { AsyncLocalStorage } from 'async_hooks';
import { randomUUID } from 'crypto';

/**
 * Ambient request-id context (AsyncLocalStorage). Mirrors the Python side
 * (`app/utils/request_context.py`): same header name and envelope key.
 */

export interface RequestContextStore {
  rootId: string;
  /**
   * Set by the auth layer once the token is verified (context middleware runs
   * before auth, so org is unknown at store-creation time). The logging
   * provider decides how, or whether, it is shown on a record.
   */
  orgId?: string;
  /**
   * The relay header of the /mcp call this request belongs to. Forwarded on calls to the other
   * services, so the role lookup they make with the same bearer can show it came from /mcp.
   */
  mcpRelay?: string;
  /** Set with `orgId`, from the same verified token, and shown the same way. */
  userId?: string;
  /** Ids bound with `bindLogContext`, added to every later line of this context. */
  logFields?: LogContextFields;
}

export const HEADER_MCP_RELAY = 'x-pipeshub-mcp-relay';

/**
 * The ids a request may add to its log lines; the names match the Python
 * side's. `org_id`, `user_id` and `request_id` are not here: only the context
 * and auth layers set those.
 */
export const LOG_CONTEXT_KEYS = [
  'conversation_id',
  'agent_id',
  'run_id',
  'connector_id',
  'connector_type',
  'record_id',
  'kb_id',
  'tool',
  'tool_call_id',
  'mcp_server_id',
  'toolset_id',
] as const;

export type LogContextFields = Partial<
  Record<(typeof LOG_CONTEXT_KEYS)[number], string | number>
>;

const LOG_CONTEXT_KEY_SET: ReadonlySet<string> = new Set(LOG_CONTEXT_KEYS);

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
 * Attach the verified org and user to the in-flight context (no-op without a context). Called by the auth layer
 * after the token is verified; mutates the current store in place so every later line of the request, the
 * error middleware's included, carries them without re-binding. Empty or non-string values are ignored.
 */
export function setContextIdentity(ids: {
  orgId?: unknown;
  userId?: unknown;
}): void {
  const ctx = storage.getStore();
  if (!ctx) return;
  const orgId =
    typeof ids.orgId === 'string' ? sanitizeRootId(ids.orgId) : undefined;
  const userId =
    typeof ids.userId === 'string' ? sanitizeRootId(ids.userId) : undefined;
  if (orgId) ctx.orgId = orgId;
  if (userId) ctx.userId = userId;
}

/**
 * Add ids to every later log line of the current context (no-op without one). Keys outside
 * `LOG_CONTEXT_KEYS` are dropped, so a caller can neither fake the identity nor overwrite a record's
 * `level` or `message`; null and undefined values are skipped so optional ids can be passed as they are.
 */
export function bindLogContext(fields: LogContextFields): void {
  const ctx = storage.getStore();
  if (!ctx) return;
  // Read as unknown: the types stop neither a JS caller nor an `as any` from passing other keys or null.
  const kept = Object.entries(fields as Record<string, unknown>).filter(
    ([key, value]) => LOG_CONTEXT_KEY_SET.has(key) && value != null,
  );
  if (kept.length > 0) {
    ctx.logFields = { ...ctx.logFields, ...Object.fromEntries(kept) };
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

/**
 * Add the root id and any /mcp relay header to outbound HTTP headers (returns a copy, never mutates).
 * With a context the root id replaces any request id already there (in any case): callers pass the
 * inbound headers on, and the service must get the sanitised root, not the client's raw id.
 */
export function injectRequestHeaders(
  headers: Record<string, string> = {},
): Record<string, string> {
  const ctx = storage.getStore();
  const out = ctx
    ? Object.fromEntries(
        Object.entries(headers).filter(
          ([name]) => name.toLowerCase() !== HEADER_REQUEST_ID,
        ),
      )
    : { ...headers };
  if (ctx) {
    out[HEADER_REQUEST_ID] = ctx.rootId;
  }
  if (ctx?.mcpRelay && !hasHeader(out, HEADER_MCP_RELAY)) {
    out[HEADER_MCP_RELAY] = ctx.mcpRelay;
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
