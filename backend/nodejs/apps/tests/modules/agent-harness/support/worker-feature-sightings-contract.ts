/**
 * What every `WorkerFeatureSightings` must do (DUR2-W9): the latest time a
 * live worker advertised each feature, on the store clock, shared by every
 * Node replica. `InMemoryWorkerFeatureSightings` (`worker-feature-sightings.test.ts`),
 * `MongoWorkerFeatureSightings` (`worker-feature-sightings.mongo.itest.ts`)
 * and `PgWorkerFeatureSightings` (`execution-store.pg.itest.ts`).
 * The suite owns the store clock; `replica()` is another Node replica over
 * the same store.
 */
import { expect } from 'chai';
import { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { WorkerFeatureSightings } from '../../../../src/modules/agent-harness/ports/worker-features';

export interface SightingsBackend {
  replica(): WorkerFeatureSightings;
}

const T0 = 1_000_000;

export function describeWorkerFeatureSightingsContract(
  name: string,
  create: (clock: StoreClock) => Promise<SightingsBackend>,
): void {
  describe(`WorkerFeatureSightings contract: ${name}`, () => {
    let now = T0;
    let backend: SightingsBackend;

    beforeEach(async () => {
      now = T0;
      backend = await create({ now: () => now });
    });

    it('WF-1: a feature never advertised has no sighting, read against the store clock', async () => {
      expect(await backend.replica().sighting('agent_code')).to.deep.equal({ seenAt: null, now: T0 });
    });

    it('WF-2: one advertisement records every feature it names at the store time, and no other', async () => {
      const sightings = backend.replica();
      now = T0 + 5_000;
      await sightings.saw(['agent_code', 'chat']);
      now = T0 + 9_000;
      expect(await sightings.sighting('agent_code')).to.deep.equal({ seenAt: T0 + 5_000, now: T0 + 9_000 });
      expect((await sightings.sighting('chat')).seenAt).to.equal(T0 + 5_000);
      expect((await sightings.sighting('executor_engine')).seenAt).to.equal(null);
    });

    it('WF-3: the latest sighting is kept: an earlier store time never moves it back', async () => {
      const sightings = backend.replica();
      now = T0 + 5_000;
      await sightings.saw(['agent_code']);
      now = T0 + 3_000;
      await sightings.saw(['agent_code']);
      expect((await sightings.sighting('agent_code')).seenAt).to.equal(T0 + 5_000);
      now = T0 + 8_000;
      await sightings.saw(['agent_code']);
      expect((await sightings.sighting('agent_code')).seenAt).to.equal(T0 + 8_000);
    });

    it('WF-4: an advertisement of no features records nothing', async () => {
      await backend.replica().saw([]);
      expect((await backend.replica().sighting('chat')).seenAt).to.equal(null);
    });

    it("WF-5: replicas share the sightings: one replica reads what a worker showed another", async () => {
      await backend.replica().saw(['inherited_labels']);
      now = T0 + 1_000;
      expect(await backend.replica().sighting('inherited_labels')).to.deep.equal({ seenAt: T0, now: T0 + 1_000 });
    });

    it('WF-6: concurrent advertisements at different store times keep the latest', async () => {
      let tick = T0;
      backend = await create({ now: () => (tick += 1_000) });
      await Promise.all(Array.from({ length: 6 }, () => backend.replica().saw(['chat'])));
      const { seenAt, now: readAt } = await backend.replica().sighting('chat');
      expect(seenAt).to.equal(readAt - 1_000);
    });
  });
}
