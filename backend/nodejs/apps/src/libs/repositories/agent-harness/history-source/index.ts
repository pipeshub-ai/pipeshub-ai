import type { HistorySource } from '../../../../modules/agent-harness/ports/history-source';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { HarnessHistorySource } from './mongo';
import { PgHarnessHistorySource } from './pg';

export const createHistorySource = (): HistorySource<Tx> =>
  pickHarnessStore<HistorySource<Tx>>('agent-harness/history-source', {
    mongo: () => new HarnessHistorySource(),
    postgres: () => new PgHarnessHistorySource(),
  });
