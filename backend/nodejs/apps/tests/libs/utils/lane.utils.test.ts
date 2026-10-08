import { expect } from 'chai';
import { readFileSync } from 'fs';
import { resolve } from 'path';

import {
  DEFAULT_FAIR_SCHEDULING_LANE_COUNT,
  laneCount,
} from '../../../src/libs/utils/lane.utils';

describe('lane.utils', () => {
  const originalLanes = process.env.FAIR_SCHEDULING_LANE_COUNT;

  afterEach(() => {
    process.env.FAIR_SCHEDULING_LANE_COUNT = originalLanes;
    if (originalLanes === undefined) delete process.env.FAIR_SCHEDULING_LANE_COUNT;
  });

  describe('laneCount', () => {
    it('defaults to eight lanes, as the Python services do', () => {
      delete process.env.FAIR_SCHEDULING_LANE_COUNT;
      expect(laneCount()).to.equal(8);
    });

    it('agrees with the Python default', () => {
      // This service pre-creates the lane streams the Python services publish
      // to and read. With different defaults, a default install pre-created
      // one set of streams and used another.
      const pythonConfig = readFileSync(
        resolve(__dirname, '../../../../../python/app/services/messaging/config.py'),
        'utf8',
      );
      const match = pythonConfig.match(
        /_env_int\("FAIR_SCHEDULING_LANE_COUNT",\s*(\d+)\)/,
      );
      expect(match, 'Python lane-count default not found').to.not.equal(null);
      expect(Number(match?.[1])).to.equal(DEFAULT_FAIR_SCHEDULING_LANE_COUNT);
    });

    it('follows FAIR_SCHEDULING_LANE_COUNT', () => {
      process.env.FAIR_SCHEDULING_LANE_COUNT = '4';
      expect(laneCount()).to.equal(4);
    });

    it('keeps the default for a value that is not a positive number', () => {
      process.env.FAIR_SCHEDULING_LANE_COUNT = 'lots';
      expect(laneCount()).to.equal(8);
    });

    it('one lane means laning is off', () => {
      process.env.FAIR_SCHEDULING_LANE_COUNT = '1';
      expect(laneCount()).to.equal(1);
    });
  });
});
