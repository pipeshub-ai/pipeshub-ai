import type { RunAdmissionStore } from '../../../../modules/agent-harness/domain/admission';
import type { CompactRequests } from '../../../../modules/agent-harness/domain/compact-requests';
import type {
  NextRunEraser,
  NextRunQueue,
} from '../../../../modules/agent-harness/domain/next-runs';
import { pickHarnessStore } from '../stores';
import { mongoServerTime } from '../time/mongo';
import { pgServerTime } from '../time/pg';
import { MongoRunAdmissionStore, MongoRunAdmissionStoreOptions } from './mongo';
import { PgRunAdmissionStore, PgRunAdmissionStoreOptions } from './pg';

export type HarnessAdmissionStore = RunAdmissionStore &
  NextRunQueue &
  NextRunEraser &
  CompactRequests;

/** What both backends take; each reads its own database clock. */
export type RunAdmissionStoreOptions = Omit<
  MongoRunAdmissionStoreOptions & PgRunAdmissionStoreOptions,
  'time' | 'transactionsAvailable'
>;

/** The public admission boundary on the selected document store. */
export const createRunAdmissionStore = (
  options: RunAdmissionStoreOptions,
): HarnessAdmissionStore =>
  pickHarnessStore<HarnessAdmissionStore>('agent-harness/run-admission-store', {
    mongo: () =>
      new MongoRunAdmissionStore({ ...options, time: mongoServerTime }),
    postgres: () => new PgRunAdmissionStore({ ...options, time: pgServerTime }),
  });
