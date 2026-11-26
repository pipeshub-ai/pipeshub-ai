import type { RunInputSeal } from '../../../../modules/agent-harness/application/run-input-seal';
import type {
  ScheduleEraser,
  ScheduleStore,
} from '../../../../modules/agent-harness/ports/schedules';
import { pickHarnessStore } from '../stores';
import { MongoScheduleStore } from './mongo';
import { PgScheduleStore } from './pg';

/** `inputs` seals the templates (GAPS-1); plaintext when omitted. */
export const createScheduleStore = (
  inputs?: RunInputSeal,
): ScheduleStore & ScheduleEraser =>
  pickHarnessStore<ScheduleStore & ScheduleEraser>('agent-harness/schedules', {
    mongo: () => new MongoScheduleStore(undefined, inputs),
    postgres: () => new PgScheduleStore(undefined, inputs),
  });
