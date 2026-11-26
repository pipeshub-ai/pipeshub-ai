import type { RunStatusReader } from '../../../../modules/agent-harness/ports/schedules';
import { pickHarnessStore } from '../stores';
import { MongoRunStatusReader } from './mongo';
import { PgRunStatusReader } from './pg';

export const createRunStatusReader = (): RunStatusReader =>
  pickHarnessStore<RunStatusReader>('agent-harness/run-status', {
    mongo: () => new MongoRunStatusReader(),
    postgres: () => new PgRunStatusReader(),
  });
