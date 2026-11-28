/**
 * The unit a control-plane store call runs in on PostgreSQL. A write joins the control-plane transaction through
 * `tx.session` (main's `Tx`, which `PgControlPlaneTransaction` hands out); a call with no session joins the unit open
 * in this async context, else opens one as the org.
 */
import { orgUnit, type PgKernelTx } from '../agent-harness/pg-documents/pg';
import type { Tx } from '../uow';

/**
 * Runs `fn` in the org's unit: the session's, else the unit open in this async context, else a new one as the bound
 * org. The org must be the one its caller bound (a request's, or a job's from its own enumeration); with none bound
 * or another, `orgUnit` refuses the call.
 */
export function cpUnit<T>(
  session: unknown,
  orgId: string,
  opts: { write: boolean },
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  return orgUnit((session ?? undefined) as Tx | undefined, orgId, opts, fn);
}

/** A bigint column as the number the documents hold. */
export const num = (value: string | number): number => Number(value);
