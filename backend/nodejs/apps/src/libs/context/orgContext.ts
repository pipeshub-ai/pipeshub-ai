import { AsyncLocalStorage } from 'node:async_hooks';
import { Logger } from '../services/logger.service';
import { getRootId } from './request-context';

/**
 * Ambient org identity for the work in flight (AsyncLocalStorage). Mirrors
 * the Python module `app/core/org_context.py`: same names, same semantics.
 *
 * Set only at trusted entry points: verified JWT / scoped token (auth
 * middleware), the consumer envelope wrapper, and jobs (`forOrg` / `system`).
 * Never from a request body, query string or header other than `Authorization`.
 *
 * Missing context fails closed: `currentOrgContext()` throws. Long-lived work
 * (timers, emitters, consumers, lazily created clients) must start inside
 * `spawnDetached`, because AsyncLocalStorage propagates into everything created
 * inside `run()` and such work would otherwise serve every later event as that
 * request's org.
 */
export type OrgContextKind = 'user' | 'service' | 'job' | 'system';

export interface OrgContext {
  /** null only for kind === 'system' */
  orgId: string | null;
  userId: string | null;
  kind: OrgContextKind;
  /** 'jwt' | 'scoped_jwt' | 'envelope' | 'legacy-payload' | 'job:<name>' | 'system:<reason>' */
  source: string;
  requestId: string;
}

export class MissingOrgContext extends Error {
  constructor(message = 'no org context is bound') {
    super(message);
    this.name = 'MissingOrgContext';
  }
}

export class SystemContextNotAllowed extends Error {
  constructor(source: string) {
    super(`system context (${source}) cannot act for one org`);
    this.name = 'SystemContextNotAllowed';
  }
}

export class OrgMismatch extends Error {
  constructor(want: string | null, have: string | null) {
    super(`cannot bind ${want} inside ${have}`);
    this.name = 'OrgMismatch';
  }
}

export const orgContextStorage = new AsyncLocalStorage<OrgContext>();

export function makeOrgContext(
  c: Omit<OrgContext, 'requestId'> & { requestId?: string },
): OrgContext {
  if ((c.kind === 'system') !== (c.orgId === null)) {
    throw new Error("orgId is required unless kind === 'system'");
  }
  return { ...c, requestId: c.requestId ?? getRootId() ?? '-' };
}

/** The ambient context, including a system one. Jobs only. */
export function currentOrgContextOrSystem(): OrgContext {
  const ctx = orgContextStorage.getStore();
  if (!ctx) throw new MissingOrgContext();
  return ctx;
}

export function currentOrgContext(): OrgContext {
  const ctx = currentOrgContextOrSystem();
  if (ctx.kind === 'system') throw new SystemContextNotAllowed(ctx.source);
  return ctx;
}

export function getCurrentOrgId(): string | undefined {
  return orgContextStorage.getStore()?.orgId ?? undefined;
}

/** Bind `ctx` for the async subtree of `fn`. A nested bind must keep the org. */
export function runWithOrgContext<T>(ctx: OrgContext, fn: () => T): T {
  const outer = orgContextStorage.getStore();
  if (outer?.orgId && ctx.orgId !== outer.orgId) {
    throw new OrgMismatch(ctx.orgId, outer.orgId);
  }
  return orgContextStorage.run(ctx, fn);
}

/** Per-org iteration inside a job. Binds from the job's own enumeration, never from input. */
export function forOrg<T>(
  orgId: string | null | undefined,
  job: string,
  fn: () => T,
): T {
  if (!orgId) throw new MissingOrgContext(`job '${job}' has no org id`);
  return runWithOrgContext(
    makeOrgContext({ orgId, userId: null, kind: 'job', source: `job:${job}` }),
    fn,
  );
}

/**
 * Work for an account the server itself resolved before any request carried a token: from a stored sign-in session, a
 * signed link, an address lookup it ran, or the organisation it is creating. Never from request input. A nested bind must
 * keep the org.
 */
export function asAccount<T>(
  orgId: string,
  userId: string,
  source: string,
  fn: () => T,
): T {
  return runWithOrgContext(
    makeOrgContext({ orgId, userId, kind: 'user', source }),
    fn,
  );
}

/** Explicit cross-org scope (enumeration, boot). A read of one org's data must still be inside `forOrg`. */
export function system<T>(reason: string, fn: () => T): T {
  return runWithOrgContext(
    makeOrgContext({
      orgId: null,
      userId: null,
      kind: 'system',
      source: `system:${reason}`,
    }),
    fn,
  );
}

/**
 * A function bound to the context it is created in (see `onBound`): every call runs in that org, or in none, whatever context
 * the caller has. The context is captured and re-entered explicitly: `AsyncLocalStorage.bind` only does so from Node 24 on
 * (before, a bound function called from inside `exit()` or from another request's callback kept the caller's context, which is
 * how a listener on a reused socket served the wrong org).
 */
export function bindOrgContext<F extends (...args: never[]) => unknown>(
  fn: F,
): F {
  const captured = orgContextStorage.getStore();
  const call = fn as unknown as (...args: unknown[]) => unknown;
  return ((...args: unknown[]) =>
    captured === undefined
      ? orgContextStorage.exit(call, ...args)
      : orgContextStorage.run(captured, call, ...args)) as unknown as F;
}

/**
 * `emitter.on(event, listener)` with the listener bound to the context it is registered in. A listener otherwise runs
 * in the context of whatever emits: a response stream on a socket that an earlier request opened and a keep-alive agent
 * reused carries that request's org.
 */
export function onBound<E extends NodeJS.EventEmitter>(
  emitter: E,
  event: string,
  listener: (...args: never[]) => unknown,
): E {
  return emitter.on(
    event,
    bindOrgContext(listener) as (...args: unknown[]) => void,
  );
}

/**
 * Start long-lived work with an empty context (timers, emitters, consumer
 * loops, lazily created clients). Request-scoped child work needs nothing:
 * it keeps the caller's context.
 */
export function spawnDetached<T>(fn: () => T): T {
  return orgContextStorage.exit(fn);
}

// --- shadow / enforce -------------------------------------------------------
// `shadow` (default) logs and counts an access with no context, or with one for a
// different org, and lets it through. `enforce` fails closed. Flip per deployment
// with ORG_CONTEXT_MODE once the shadow counters stay at zero.

/** pipeshub_org_context_violations_total{kind,site} */
export const orgContextViolations = new Map<string, number>();
const logged = new Set<string>();

/** true in enforce mode. An unknown ORG_CONTEXT_MODE throws (never silently shadow). */
export function validateMode(): boolean {
  const mode =
    (process.env.ORG_CONTEXT_MODE ?? 'shadow').trim().toLowerCase() || 'shadow';
  if (mode !== 'shadow' && mode !== 'enforce') {
    throw new Error(
      `ORG_CONTEXT_MODE must be 'shadow' or 'enforce', got '${mode}'`,
    );
  }
  return mode === 'enforce';
}

export const enforcing = validateMode;

/** The org-less bootstrap token names the literal org "system": counted apart, still refused in enforce mode. */
export const SYSTEM_ORG_ID = 'system';

function callerSite(): string {
  const frame = new Error().stack?.split('\n')[3]?.trim();
  return frame ?? '?';
}

/**
 * Check an explicit org argument against the ambient context at an org-scoped choke point.
 * Returns the explicit org (or the ambient one when none was given). Shadow mode never
 * throws; the first violation per (kind, site) is logged with the call site, every one is counted.
 */
export function ambientOrg(
  explicitOrgId: string | null | undefined,
  site: string,
): string | undefined {
  const ctx = orgContextStorage.getStore();
  let kind: 'missing' | 'mismatch' | 'system_token' | undefined;
  if (!ctx || ctx.orgId === null) kind = 'missing';
  else if (explicitOrgId != null && explicitOrgId !== ctx.orgId)
    kind = ctx.orgId === SYSTEM_ORG_ID ? 'system_token' : 'mismatch';
  if (kind === undefined) return explicitOrgId ?? ctx?.orgId ?? undefined;
  const label = `${kind}:${site}`;
  orgContextViolations.set(label, (orgContextViolations.get(label) ?? 0) + 1);
  if (enforcing()) {
    if (kind === 'missing')
      throw new MissingOrgContext(`${site}: no org context`);
    throw new OrgMismatch(explicitOrgId ?? null, ctx?.orgId ?? null);
  }
  if (!logged.has(label)) {
    logged.add(label);
    Logger.getInstance().warn(`org_context_${kind}`, {
      component: 'OrgContext',
      site,
      caller: callerSite(),
    });
  }
  return explicitOrgId ?? undefined;
}
