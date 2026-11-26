import type { MigrationLedger } from '../../../../modules/control-plane/ports/data-migration';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoMigrationLedger } from './mongo';
import { PgMigrationLedger } from './pg';

export const createMigrationLedger = (): MigrationLedger =>
  pickHarnessStore<MigrationLedger>('control-plane/migration-ledger', {
    mongo: () => new MongoMigrationLedger(),
    postgres: () => new PgMigrationLedger(),
  });
