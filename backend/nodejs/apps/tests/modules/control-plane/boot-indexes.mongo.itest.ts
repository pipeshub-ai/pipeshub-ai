/**
 * CPD-7 FB-5: CPD-4's boot step (`preparePolicyStore`, every install) builds
 * every control-plane index on a fresh database: CPD-2's catalog, CPD-4's
 * policy layers and CPD-7's installations, history, grants and ledger.
 * Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { CONTROL_PLANE_MIGRATIONS } from '../../../src/modules/control-plane';
import { preparePolicyStore } from '../../../src/modules/agent-harness/adapters/configuration/policy-backing';
import { harnessMongoUri } from '../agent-harness/support/harness-mongo';


describe('CPD-7 control-plane indexes at boot', function () {
  this.timeout(60_000);

  before(async () => {
    await mongoose.connect(harnessMongoUri(), { dbName: `cp_boot_it_${Date.now()}_${process.pid}` });
  });
  after(async () => {
    if (mongoose.connection.readyState === mongoose.ConnectionStates.connected) await mongoose.connection.dropDatabase();
    await mongoose.disconnect();
  });

  it('a fresh database has every control-plane index after boot', async () => {
    const errors: unknown[] = [];
    await preparePolicyStore({ info: () => undefined, error: (...args: unknown[]) => errors.push(args) });
    expect(errors).to.deep.equal([]);
    const db = mongoose.connection.db!;
    const missing: string[] = [];
    for (const migration of CONTROL_PLANE_MIGRATIONS) {
      for (const index of migration.indexes) {
        const names = (await db.collection(index.collection).indexes()).map((found) => found.name);
        if (!names.includes(index.name)) missing.push(`${index.collection}.${index.name}`);
      }
    }
    expect(missing).to.deep.equal([]);
    expect(CONTROL_PLANE_MIGRATIONS.map((migration) => migration.id)).to.include.members([
      'control-plane-catalog',
      'control-plane-installations',
      'control-plane-policy-store',
    ]);
  });
});
