import type { BudgetLedgerStore } from '../../../../modules/agent-harness/ports/budget-ledger';
import type { CostEntryWriter } from '../../../../modules/agent-harness/ports/cost-ledger';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { mongoServerTime } from '../time/mongo';
import { pgServerTime } from '../time/pg';
import { MongoBudgetLedger } from './mongo';
import { PgBudgetLedger } from './pg';

export type HarnessBudgetLedger = BudgetLedgerStore &
  Pick<MongoBudgetLedger, 'admitChildIn'>;

/** `costs`: where a commit writes its call's cost entry, in the commit's own transaction (C9.06). */
export const createBudgetLedger = (
  costs: CostEntryWriter<Tx> | null = null,
): HarnessBudgetLedger =>
  pickHarnessStore<HarnessBudgetLedger>('agent-harness/budget-ledger', {
    mongo: () => new MongoBudgetLedger(mongoServerTime, costs),
    postgres: () => new PgBudgetLedger(pgServerTime, costs),
  });
