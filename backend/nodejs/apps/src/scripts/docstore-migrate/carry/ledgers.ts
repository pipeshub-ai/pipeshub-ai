/**
 * The spend ledger (R46-CARRY, R45CARRY-FU-2), carried as one unit: budgets, reservations and holds are enforced from the
 * accounts, so a half-moved ledger would count a spend twice or not at all.
 *
 *  - `agentHarnessBudgetAccounts` -> `harness.budget_accounts` (by org and run, `PgBudgetLedger`): the account in `doc`; the
 *    MongoDB id `<org>:<run>` is given back at a rollback;
 *  - `agentHarnessBudgetReservations` -> `harness.budget_reservations` (by org, run and key): the reservation in `doc`, its state,
 *    expiry and hold flag as the store's typed columns (`hold` is written as `hold === true`).
 */
import { docTableCarrier, type DocTableSpec } from './doc-table';

export const LEDGER_TABLES: DocTableSpec[] = [
  {
    collection: 'agentHarnessBudgetAccounts',
    table: 'harness.budget_accounts',
    orgPath: 'orgId',
    key: [{ col: 'run_id', path: 'runId', kind: 'text' }],
    columns: [{ col: 'root_run_id', path: 'rootRunId', kind: 'text' }],
    doc: { omit: [] },
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.runId)}`,
      oid: false,
    }),
  },
  {
    collection: 'agentHarnessBudgetReservations',
    table: 'harness.budget_reservations',
    orgPath: 'orgId',
    key: [
      { col: 'run_id', path: 'runId', kind: 'text' },
      { col: 'key', path: 'key', kind: 'text' },
    ],
    columns: [
      { col: 'root_run_id', path: 'rootRunId', kind: 'text' },
      { col: 'state', path: 'state', kind: 'text' },
      {
        col: 'hold',
        path: 'hold',
        kind: 'bool',
        from: (src) => src.doc.hold === true,
      },
      { col: 'expires_at_ms', path: 'expiresAt', kind: 'int' },
    ],
    doc: { omit: [] },
    newId: (doc) => ({
      id: `${String(doc.orgId)}:${String(doc.runId)}:${String(doc.key)}`,
      oid: false,
    }),
  },
];

export const LEDGERS = docTableCarrier(
  'ledgers',
  'the spend ledger: budget accounts, reservations and holds, moved together so nothing is counted twice or lost',
  LEDGER_TABLES,
);
