/**
 * Helpers shared by the PostgreSQL repositories: ids, joining the unit of work that is already open, the org
 * the unit runs as, and the small conversions between the application's values and column values.
 */
import { Tx as PgKernelTx, activeUnit } from '../db';
import {
  currentOrgContext,
  getCurrentOrgId,
  makeOrgContext,
  runWithOrgContext,
} from '../context/orgContext';
import { InternalServerError } from '../errors/http.errors';
import { PgUnitOfWork, kernelTx } from './pg';
import type { Tx } from './uow';

export { newObjectId } from './ids';

const HEX24 = /^[0-9a-fA-F]{24}$/;

/** The stored form of an id, or undefined when it is not a well-formed id (such an id matches nothing). */
export function oidOf(id: unknown): string | undefined {
  const s = String(id);
  return HEX24.test(s) ? s.toLowerCase() : undefined;
}

/** Only the well-formed ids of a list, in stored form. */
export function oidsOf(ids: readonly unknown[]): string[] {
  return ids.map(oidOf).filter((x): x is string => x !== undefined);
}

/**
 * Runs `fn` in the caller's unit of work: the `tx` it was handed, else the unit already open in this async context
 * (a unit cannot start inside another), else a new one. `fresh` always starts a new unit and so refuses to run inside
 * another: the run lease must never share a transaction with a turn.
 */
export async function inUnit<T>(
  tx: Tx | undefined,
  opts: { write: boolean; fresh?: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  if (tx && !opts.fresh) return fn(kernelTx(tx));
  const open = opts.fresh ? undefined : activeUnit();
  if (open) return fn(open);
  return new PgUnitOfWork().run((t) => fn(kernelTx(t)), {
    readOnly: !opts.write,
  });
}

/** The kernel's unit handle, for repositories that only need its type. */
export type KernelTx = PgKernelTx;

/**
 * A unit that runs before any organisation is chosen (`app.org_id` is empty): for the identity functions that answer
 * sign-in questions about an address. It never joins another unit, and it fails when one is open in this context.
 */
export function inPreOrgUnit<T>(
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  return new PgUnitOfWork().run((t) => fn(kernelTx(t)), {
    readOnly: !opts.write,
    preOrg: true,
  });
}

/**
 * The caller passes the org it believes it is acting for; the unit runs as the ambient org. A difference is a bug
 * (a callback running under another request's context): fail instead of reading or writing the wrong org.
 */
export function assertOrg(k: PgKernelTx, orgId: string): void {
  if (k.orgId !== orgId)
    throw new InternalServerError(
      'the organisation of the call differs from the one the unit of work runs as',
    );
}

/** Same for the user, where the database reads it from the unit's context (`app.user_id`) rather than from an argument. */
export function assertUser(userId: string): void {
  if (currentOrgContext().userId !== userId)
    throw new InternalServerError(
      'the user of the call differs from the one the unit of work runs as',
    );
}

export const dateOf = (ms: number | undefined | null): Date | null =>
  ms === undefined || ms === null ? null : new Date(ms);

/** A SQL fragment builder for the few list queries whose conditions vary: values go to `$n` parameters, never into the text. */
export class Params {
  readonly values: unknown[] = [];
  add(v: unknown): string {
    this.values.push(v);
    return `$${String(this.values.length)}`;
  }
}

/** `%` and `_` and the escape character, made literal for LIKE/ILIKE. */
export const likeEscape = (s: string): string => s.replace(/[\\%_]/g, '\\$&');

/** The text a caller regex-escaped, back to plain text (the callers pass `escapeRegex(search)`). */
export const unescapeRegex = (s: string): string =>
  s.replace(/\\([\s\S])/g, '$1');

/** Replaces `@{NAME}` markers in a static statement; the values are SQL fragments built from constants and `$n` placeholders. */
export const fill = (text: string, macros: Record<string, string>): string =>
  Object.entries(macros).reduce(
    (t, [name, sql]) => t.split(`@{${name}}`).join(sql),
    text,
  );

/**
 * A lookup keyed by a secret the caller holds (a token hash, a client id, a Slack thread) before any organisation is chosen. The vetted
 * lookup functions are global, so they run in the open unit when there is one, else in a unit with no org.
 */
export function lookupUnit<T>(
  write: boolean,
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const open = activeUnit();
  return open ? fn(open) : inPreOrgUnit({ write }, fn);
}

/**
 * The org a call keyed by a secret runs for: the ambient org when there is one (the call then finds only that org's rows), else the
 * org the vetted lookup returned for the key. Never an org the caller named.
 */
export async function orgForKey(
  resolve: () => Promise<string | null>,
): Promise<string | null> {
  return getCurrentOrgId() ?? resolve();
}

/** Runs `fn` as `orgId` unless the ambient context is already that org. `orgId` comes from `orgForKey`. */
export function asOrg<T>(orgId: string, fn: () => Promise<T>): Promise<T> {
  if (getCurrentOrgId() === orgId) return fn();
  return runWithOrgContext(
    makeOrgContext({
      orgId,
      userId: null,
      kind: 'service',
      source: 'lookup',
    }),
    fn,
  );
}
