/**
 * Which organisation a call that holds only a secret (a client id, a token hash, a code) runs for. The token endpoints have no
 * session. An install has one organisation: a call with none bound runs as it, and with one bound the ambient org is used, so a
 * key of another org simply finds nothing. An extension that serves several organisations replaces `IdentityLookup` with one
 * that finds the owner of a key.
 */
import type { Tx as PgKernelTx } from '../../db';
import { getIdentityPort } from '../identity';
import { asOrg, assertOrg, inUnit, orgForKey } from '../pg-util';
import type { Tx } from '../uow';

/** A key the stores accept (1..200 characters, no U+0000); anything else matches nothing. */
export const goodKey = (v: unknown): v is string =>
  typeof v === 'string' &&
  v.length >= 1 &&
  v.length <= 200 &&
  !v.includes('\u0000');

/** The install's organisation, whatever the key: the same answer for a client id, a token hash and a code. */
export const installOrg = (_key?: unknown): Promise<string | null> =>
  getIdentityPort().oldestOrgId();
export const clientOrg = installOrg;
export const accessOrg = installOrg;
export const refreshOrg = installOrg;

/** Runs `fn` for the org a trusted caller names (an org the server itself chose); a different ambient org is a bug and throws. */
export function inOrgUnit<T>(
  orgId: string,
  tx: Tx | undefined,
  write: boolean,
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  return asOrg(orgId, () =>
    inUnit(tx, { write }, (k) => {
      assertOrg(k, orgId);
      return fn(k);
    }),
  );
}

/** Runs `fn` for the org the key belongs to (see above); `none` when there is no organisation. */
export async function inKeyUnit<T>(
  find: () => Promise<string | null>,
  write: boolean,
  none: T,
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  const org = await orgForKey(find);
  if (!org) return none;
  return asOrg(org, () => inUnit(undefined, { write }, fn));
}
