import type { RunStateReader } from '../../../../modules/agent-harness/ports/run-state';
import { pickHarnessStore } from '../stores';
import { MongoRunState } from './mongo';
import { PgRunState } from './pg';

export const createRunState = (): RunStateReader =>
  pickHarnessStore<RunStateReader>('agent-harness/run-state', {
    mongo: () => new MongoRunState(),
    postgres: () => new PgRunState(),
  });
