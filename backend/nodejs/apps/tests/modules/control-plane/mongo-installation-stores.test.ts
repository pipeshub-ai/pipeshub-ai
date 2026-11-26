/**
 * CPD-7: CP-SKEL's InstallationStore and GrantStore contracts, the capability
 * ledger contract and the migration cases on the Mongo adapters over in-memory
 * collections, so the unit lane checks what the adapters ask Mongo for. The
 * replica set runs the same suites in the `.mongo.itest.ts` files.
 */
import sinon from 'sinon';
import type { Model } from 'mongoose';
import {
  CpCapabilityLedgerModel,
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { MongoCapabilityLedger } from '../../../src/libs/repositories/control-plane/capability-ledger/mongo';
import { MongoGrantStore } from '../../../src/libs/repositories/control-plane/grant-store/mongo';
import { MongoInstallationStore } from '../../../src/libs/repositories/control-plane/installations/mongo';
import { MigrationLedgerModel } from '../../../src/libs/repositories/control-plane/policy/schema/policy.schema';
import { describeCapabilityLedgerContract } from './support/capability-ledger-contract';
import { describeCapabilityMigrationCases } from './support/capability-migration-cases';
import type { CapabilityWorld } from './support/capability-world';
import { fakeCollections } from './support/fake-mongo-models';
import { describeGrantStoreContract } from './support/grant-store-contract';
import { auditedControlPlane } from './support/in-memory';
import { describeInstallationStoreContract } from './support/installation-store-contract';
import type { StoreWorld } from './support/store-world';

const collections = () =>
  fakeCollections({
    installations: CpInstallationModel,
    history: CpInstallationHistoryModel,
    grants: CpGrantModel,
    ledger: CpCapabilityLedgerModel,
    migrated: MigrationLedgerModel,
  } as unknown as Record<'installations' | 'history' | 'grants' | 'ledger' | 'migrated', Model<never>>);

function world<S>(store: S, pick: 'installations' | 'grants'): StoreWorld<S> {
  const fake = collections();
  const controlPlane = auditedControlPlane();
  return {
    store,
    roundTrips: () => fake[pick].reads,
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
  };
}

async function capabilityWorld(): Promise<CapabilityWorld> {
  collections();
  const controlPlane = auditedControlPlane();
  return {
    installations: new MongoInstallationStore(),
    grants: new MongoGrantStore(),
    ledger: new MongoCapabilityLedger(),
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
  };
}

describe('CPD-7 Mongo installation, grant and ledger adapters over in-memory collections', () => {
  afterEach(() => sinon.restore());

  describeInstallationStoreContract('MongoInstallationStore (in-memory collections)', async () =>
    world(new MongoInstallationStore(), 'installations'),
  );
  describeGrantStoreContract('MongoGrantStore (in-memory collections)', async () => world(new MongoGrantStore(), 'grants'));
  describeCapabilityLedgerContract('MongoCapabilityLedger (in-memory collections)', capabilityWorld);
  describeCapabilityMigrationCases('Mongo stores (in-memory collections)', capabilityWorld);
});
