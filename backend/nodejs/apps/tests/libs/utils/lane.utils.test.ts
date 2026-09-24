import { expect } from 'chai';

import { laneStreamFor, stableLane } from '../../../src/libs/utils/lane.utils';

describe('lane.utils', () => {
  const originalBroker = process.env.MESSAGE_BROKER;
  const originalLanes = process.env.FAIR_SCHEDULING_LANE_COUNT;

  afterEach(() => {
    process.env.MESSAGE_BROKER = originalBroker;
    process.env.FAIR_SCHEDULING_LANE_COUNT = originalLanes;
    if (originalBroker === undefined) delete process.env.MESSAGE_BROKER;
    if (originalLanes === undefined) delete process.env.FAIR_SCHEDULING_LANE_COUNT;
  });

  describe('stableLane', () => {
    // Pinned vectors shared with the Python side. Python publishes most
    // record events and this service publishes some, so a disagreement would
    // put one connector on two different lanes depending on which service
    // produced the event, and the consumer's per-lane view would stop meaning
    // anything. The same pairs are asserted in
    // backend/python/tests/unit/services/messaging/test_lane_router.py.
    it('agrees with the Python lane hash', () => {
      expect(stableLane('conn-1', 8)).to.equal(5);
      expect(stableLane('connector-42', 8)).to.equal(4);
      expect(stableLane('org-1', 8)).to.equal(4);
    });

    it('is stable across calls', () => {
      const first = Array.from({ length: 20 }, (_u, i) => stableLane(`c${i}`, 16));
      const second = Array.from({ length: 20 }, (_u, i) => stableLane(`c${i}`, 16));
      expect(first).to.deep.equal(second);
    });

    it('stays within range and uses the whole space', () => {
      const lanes = new Set(
        Array.from({ length: 200 }, (_u, i) => stableLane(`connector-${i}`, 16)),
      );
      expect(lanes.size).to.equal(16);
      expect([...lanes].every((l) => l >= 0 && l < 16)).to.be.true;
    });

    it('collapses to lane 0 when laning is off', () => {
      expect(stableLane('anything', 1)).to.equal(0);
    });
  });

  describe('laneStreamFor', () => {
    it('routes to a lane stream on Redis', () => {
      process.env.MESSAGE_BROKER = 'redis';
      process.env.FAIR_SCHEDULING_LANE_COUNT = '8';
      expect(laneStreamFor('record-events', 'conn-1')).to.equal(
        'record-events.5',
      );
    });

    it('leaves the topic alone on Kafka, where the key selects the partition', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      process.env.FAIR_SCHEDULING_LANE_COUNT = '8';
      expect(laneStreamFor('record-events', 'conn-1')).to.equal('record-events');
    });

    it('leaves the topic alone when laning is off', () => {
      process.env.MESSAGE_BROKER = 'redis';
      process.env.FAIR_SCHEDULING_LANE_COUNT = '1';
      expect(laneStreamFor('record-events', 'conn-1')).to.equal('record-events');
    });
  });
});
