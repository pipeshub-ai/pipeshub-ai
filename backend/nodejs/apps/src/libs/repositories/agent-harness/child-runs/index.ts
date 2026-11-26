import type { ChildRunStore } from '../../../../modules/agent-harness/ports/child-runs';
import { pickHarnessStore } from '../stores';
import { mongoServerTime } from '../time/mongo';
import { pgServerTime } from '../time/pg';
import { MongoChildRunStore, MongoChildRunStoreOptions } from './mongo';
import { PgChildRunStore, PgChildRunStoreOptions } from './pg';

/** What both backends take; each reads its own database clock. */
export type ChildRunStoreOptions = Omit<
  MongoChildRunStoreOptions,
  'time' | 'budgets'
> &
  Omit<PgChildRunStoreOptions, 'time'>;

/** Child admission on the selected document store (P6.1). */
export const createChildRunStore = (
  options: ChildRunStoreOptions,
): ChildRunStore =>
  pickHarnessStore<ChildRunStore>('agent-harness/child-runs', {
    mongo: () => new MongoChildRunStore({ ...options, time: mongoServerTime }),
    postgres: () => new PgChildRunStore({ ...options, time: pgServerTime }),
  });
