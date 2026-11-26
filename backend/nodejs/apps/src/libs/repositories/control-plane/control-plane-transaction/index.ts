import type {
  ChangeOutbox,
  ConfigAuditLog,
} from '../../../../modules/control-plane/ports/config-audit-log';
import type { ControlPlaneLog } from '../../../../modules/control-plane/ports/log';
import type { ControlPlaneTransaction } from '../../../../modules/control-plane/ports/transaction';
import { pickHarnessStore } from '../../agent-harness/stores';
import { PgControlPlaneTransaction } from './pg';

/**
 * The control-plane write transaction for the document store. On MongoDB the caller picks between the replica-set
 * transaction and ordered steps (`mongo`); PostgreSQL always commits a write in one unit.
 */
export const createControlPlaneWriteTransaction = (options: {
  audit: ConfigAuditLog;
  outbox?: ChangeOutbox;
  log?: ControlPlaneLog;
  mongo: () => ControlPlaneTransaction;
}): ControlPlaneTransaction =>
  pickHarnessStore<ControlPlaneTransaction>(
    'control-plane/control-plane-transaction',
    {
      mongo: options.mongo,
      postgres: () =>
        new PgControlPlaneTransaction({
          audit: options.audit,
          ...(options.outbox !== undefined ? { outbox: options.outbox } : {}),
          ...(options.log !== undefined ? { log: options.log } : {}),
        }),
    },
  );
