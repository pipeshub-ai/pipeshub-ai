import type { CapabilityLedger } from '../../../../modules/control-plane/ports/capability-ledger';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoCapabilityLedger } from './mongo';
import { PgCapabilityLedger } from './pg';

export const createCapabilityLedger = (): CapabilityLedger =>
  pickHarnessStore<CapabilityLedger>('control-plane/capability-ledger', {
    mongo: () => new MongoCapabilityLedger(),
    postgres: () => new PgCapabilityLedger(),
  });
