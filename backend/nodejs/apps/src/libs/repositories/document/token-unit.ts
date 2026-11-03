/**
 * A unit of work of its own for the write-state calls (`beginWrite`, `renewWrite`, `endWrite`, `bumpSeq`). Those calls must be
 * visible to every reader at once and must survive a rollback of whatever the caller is doing, so they never join the caller's
 * unit: this runs outside it, on its own pooled connection, with the full kernel prologue (role and `app.org_id`, so row-level
 * security still applies) and its own COMMIT. The statement is bounded to one second by the kernel's own statement timeout.
 *
 * A caller that holds a connection and waits on this needs a second one from the pool: the storage controllers never run inside
 * a unit (a lint test keeps it so).
 */
import { outsideUnit } from '../../db';
import type { Tx as PgKernelTx } from '../../db';
import { PgUnitOfWork, kernelTx } from '../pg';
import { assertOrg } from '../pg-util';

export const TOKEN_STATEMENT_TIMEOUT_MS = 1000;

export function ownUnit<T>(
  orgId: string,
  write: boolean,
  fn: (k: PgKernelTx) => Promise<T>,
): Promise<T> {
  return outsideUnit(() =>
    new PgUnitOfWork().run(
      (t) => {
        const k = kernelTx(t);
        assertOrg(k, orgId);
        return fn(k);
      },
      { readOnly: !write, timeoutMs: TOKEN_STATEMENT_TIMEOUT_MS },
    ),
  );
}
