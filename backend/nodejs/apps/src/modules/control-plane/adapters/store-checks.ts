import type { ZodType } from 'zod/v3';
import { ControlPlaneRevisionConflictError } from '../domain/errors';
import { InvalidObject, WrongOrg } from '../domain/store-errors';
import type { StoreTx } from '../ports/store-tx';

/** The object as its generated schema accepts it, in the transaction's org (CPD2-48's rule for every store). */
export function checkedObject<T extends { orgId: string }>(
  tx: StoreTx,
  schema: ZodType<T>,
  value: T,
  what: string,
): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    throw new InvalidObject(
      `${what}: ${parsed.error.issues[0]?.message ?? 'invalid'}`,
    );
  }
  if (parsed.data.orgId !== tx.orgId) {
    throw new WrongOrg(
      `${what} is in org ${parsed.data.orgId}, the transaction in ${tx.orgId}`,
    );
  }
  return parsed.data;
}

export function compareAndSet(
  current: number,
  expected: number,
  what: string,
): void {
  if (current !== expected) {
    throw new ControlPlaneRevisionConflictError(
      `${what} is at revision ${current}, not ${expected}`,
      String(current),
    );
  }
}
