/**
 * What every PostgreSQL harness store shares: the stored form of a document (`encodeDoc` / `decodeDoc`), the units of
 * work a store call runs in (`orgUnit`, `jobsUnit`, `deploymentUnit`), the database clock (`dbNowMs`) and the region
 * stamp. The tables are the harness branch of `backend/db` (`versions/harness`): org_id leads every key, `doc` holds
 * the document as the Mongo store keeps it, and the fields a query reads are typed or generated columns.
 */
import { orgContextStorage, system } from '../../../context/orgContext';
import {
  Tx as PgKernelTx,
  activeUnit,
  withJobsUow,
  withUow,
} from '../../../db';
import { requireBoundOrg } from '../durable-transaction/bound-org';
import { InternalServerError } from '../../../errors/http.errors';
import { deploymentRegion } from '../../../../modules/agent-harness/domain/region';
import { mapPgStoreError } from '../../pg';
import { assertOrg, inUnit } from '../../pg-util';
import type { Tx } from '../../uow';

export type { PgKernelTx };

let region: string | null = null;
/** The deployment's region, stamped on every row a harness store inserts (OWD-12); read once per process. */
export const rowRegion = (): string => (region ??= deploymentRegion());

/**
 * The org-scoped unit a store call runs in: the caller's `tx`, else the unit already open in this async context, else
 * a new one as the bound organisation. The org the caller names must be the one the unit runs as (row policies would
 * otherwise hide every row and refuse every write): a difference is a wiring fault, never another org's data. With no
 * unit open and no org bound the call is refused before a connection is taken (`requireBoundOrg`).
 */
export async function orgUnit<T>(
  tx: Tx | undefined,
  orgId: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  if (tx === undefined && activeUnit() === undefined) requireBoundOrg(orgId);
  return inUnit(tx, opts, (k) => {
    assertOrg(k, orgId);
    return fn(k);
  });
}

/**
 * Work that reads or writes across organisations (the run queue, the sweeps, the deployment-wide reports): as the jobs
 * role, whose row policy admits every org, in a system context when the caller has none. Statements name `org_id` in
 * every predicate that is meant for one org.
 */
export function jobsUnit<T>(
  reason: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const run = async (): Promise<T> => {
    try {
      return await withJobsUow({ write: opts.write }, fn);
    } catch (error) {
      throw mapPgStoreError(error);
    }
  };
  return orgContextStorage.getStore() ? run() : system(reason, run);
}

/** Deployment-wide plumbing with no org (`internal` tables: the leader leases): a unit with no organisation. */
export async function deploymentUnit<T>(
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  try {
    return await withUow({ write: opts.write, preOrg: true }, fn);
  } catch (error) {
    throw mapPgStoreError(error);
  }
}

/** Milliseconds since the epoch on the database's clock: leases and deadlines are judged on one clock [R-8]. */
export async function dbNowMs(k: PgKernelTx): Promise<number> {
  const { rows } = await k.query<{ now: string }>(
    `/* pipeshub:harness.clock_now */ SELECT (extract(epoch FROM clock_timestamp()) * 1000)::bigint::text AS now`,
  );
  const now = Number(rows[0]?.now);
  if (!Number.isFinite(now))
    throw new InternalServerError('the database clock did not answer');
  return now;
}

const TAGS = new Set(['$date', '$bin', '$str', '$num']);

/**
 * A document as `jsonb` holds it, losslessly: a Date is `{"$date": ms}`, bytes are `{"$bin": base64}`, a string with
 * U+0000 (which `jsonb` cannot hold) is `{"$str": base64}`, a non-finite number is `{"$num": "NaN"}`, and a key of the
 * document's own that starts with `$` gets one more `$`, so no stored value can be read back as a tag. `undefined`
 * fields are left out, as Mongo leaves them out.
 */
export function encodeDoc(value: unknown): unknown {
  if (value === null) return null;
  if (value instanceof Date) return { $date: value.getTime() };
  if (Buffer.isBuffer(value) || value instanceof Uint8Array)
    return { $bin: Buffer.from(value).toString('base64') };
  if (typeof value === 'string')
    return value.includes('\u0000')
      ? { $str: Buffer.from(value, 'utf8').toString('base64') }
      : value;
  if (typeof value === 'number')
    return Number.isFinite(value) ? value : { $num: String(value) };
  if (Array.isArray(value))
    return value.map((item) => (item === undefined ? null : encodeDoc(item)));
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(
      value as Record<string, unknown>,
    )) {
      if (item === undefined) continue;
      out[encodeKey(key)] = encodeDoc(item);
    }
    return out;
  }
  return value;
}

/** The document `encodeDoc` stored. */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-type-parameters -- callers name the document type they stored
export function decodeDoc<T = unknown>(value: unknown): T {
  return decode(value) as T;
}

function decode(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(decode);
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 1) {
    const [tag, inner] = entries[0] as [string, unknown];
    if (TAGS.has(tag)) {
      if (tag === '$date') return new Date(inner as number);
      if (tag === '$bin') return Buffer.from(inner as string, 'base64');
      if (tag === '$str')
        return Buffer.from(inner as string, 'base64').toString('utf8');
      return Number(inner);
    }
  }
  const out: Record<string, unknown> = {};
  for (const [key, item] of entries) out[decodeKey(key)] = decode(item);
  return out;
}

const encodeKey = (key: string): string => {
  const escaped = key.startsWith('$') ? `$${key}` : key;
  return escaped.includes('\u0000')
    ? `$k:${Buffer.from(escaped, 'utf8').toString('base64')}`
    : escaped;
};

const decodeKey = (key: string): string => {
  const unwrapped = key.startsWith('$k:')
    ? Buffer.from(key.slice(3), 'base64').toString('utf8')
    : key;
  return unwrapped.startsWith('$$') ? unwrapped.slice(1) : unwrapped;
};

/** `encodeDoc` as the JSON text a `jsonb` parameter takes. */
export const docParam = (value: unknown): string =>
  JSON.stringify(encodeDoc(value));
