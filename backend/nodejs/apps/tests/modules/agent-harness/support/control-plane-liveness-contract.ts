/**
 * What every `ControlPlaneLiveness` must do (O-6a / C-13): one shared record
 * of when a sweeper last saw the control plane, and a holdoff that survives a
 * leader change. `InMemoryControlPlaneLiveness` (`control-plane-liveness.test.ts`)
 * and `MongoControlPlaneLiveness` (`control-plane-liveness.mongo.itest.ts`).
 * The suite owns the store clock; `replica()` is another Node replica over
 * the same store.
 */
import { expect } from 'chai';
import { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { ControlPlaneLiveness } from '../../../../src/modules/agent-harness/ports/control-plane-liveness';

export interface LivenessBackend {
  replica(): ControlPlaneLiveness;
}

const T0 = 1_000_000;

export function describeControlPlaneLivenessContract(
  name: string,
  create: (clock: StoreClock) => Promise<LivenessBackend>,
): void {
  describe(`ControlPlaneLiveness contract: ${name}`, () => {
    let now = T0;
    let backend: LivenessBackend;

    beforeEach(async () => {
      now = T0;
      backend = await create({ now: () => now });
    });

    it('CL-1: the first ever observation has no previous sighting and no holdoff', async () => {
      expect(await backend.replica().observe()).to.deep.equal({
        now: T0,
        previousSeenAt: null,
        holdoffUntil: 0,
      });
    });

    it('CL-2: each observation answers when the control plane was seen before it, on the store clock', async () => {
      const liveness = backend.replica();
      await liveness.observe();
      now = T0 + 30_000;
      expect(await liveness.observe()).to.deep.equal({
        now: T0 + 30_000,
        previousSeenAt: T0,
        holdoffUntil: 0,
      });
    });

    it('CL-3: a holdoff only ever extends', async () => {
      const liveness = backend.replica();
      await liveness.observe();
      await liveness.holdOff(T0 + 60_000);
      await liveness.holdOff(T0 + 10_000);
      expect((await liveness.observe()).holdoffUntil).to.equal(T0 + 60_000);
      await liveness.holdOff(T0 + 90_000);
      expect((await liveness.observe()).holdoffUntil).to.equal(T0 + 90_000);
    });

    it('CL-4: a holdoff set before any observation holds from the first one', async () => {
      await backend.replica().holdOff(T0 + 60_000);
      expect(await backend.replica().observe()).to.deep.equal({
        now: T0,
        previousSeenAt: null,
        holdoffUntil: T0 + 60_000,
      });
    });

    it("CL-5: replicas share one record: a new leader sees the old leader's sighting and holdoff", async () => {
      const a = backend.replica();
      await a.observe();
      await a.holdOff(T0 + 120_000);
      now = T0 + 5_000;
      expect(await backend.replica().observe()).to.deep.equal({
        now: T0 + 5_000,
        previousSeenAt: T0,
        holdoffUntil: T0 + 120_000,
      });
    });

    it('CL-6: concurrent observations lose none: each previous sighting is exactly one other observation', async () => {
      let tick = T0;
      backend = await create({ now: () => (tick += 1) });
      const observations = await Promise.all(
        Array.from({ length: 8 }, () => backend.replica().observe()),
      );
      const nows = observations.map((observation) => observation.now);
      expect(new Set(nows).size).to.equal(8);
      const previous = observations.map((observation) => observation.previousSeenAt);
      expect(previous.filter((seen) => seen === null)).to.have.length(1);
      const seen = previous.filter((value): value is number => value !== null);
      expect(new Set(seen).size).to.equal(7);
      expect(seen.every((value) => nows.includes(value))).to.equal(true);
    });

    it('CL-7: concurrent holdoffs keep the latest', async () => {
      await backend.replica().observe();
      await Promise.all(
        [30_000, 90_000, 60_000].map((ms) => backend.replica().holdOff(T0 + ms)),
      );
      expect((await backend.replica().observe()).holdoffUntil).to.equal(T0 + 90_000);
    });
  });
}
