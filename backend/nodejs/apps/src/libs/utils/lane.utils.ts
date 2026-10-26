import { ENV_FAIR_SCHEDULING_LANE_COUNT } from '../constants/messaging.constants';
import { parsePositiveIntSafe } from './env.utils';

/**
 * Fair-scheduling lanes for the indexing topic.
 *
 * On Redis Streams a lane is its own stream (`record-events.3`). This service
 * publishes no record events; the Python services place each connector on its
 * lane. It only pre-creates the lane streams (see redis-streams.service.ts), so
 * this count must agree with Python's `FAIR_SCHEDULING_LANE_COUNT` default.
 */

export const DEFAULT_FAIR_SCHEDULING_LANE_COUNT = 8;

/** Lane count for the indexing topic; 1 disables laning. */
export function laneCount(): number {
  return parsePositiveIntSafe(
    process.env[ENV_FAIR_SCHEDULING_LANE_COUNT],
    DEFAULT_FAIR_SCHEDULING_LANE_COUNT,
    ENV_FAIR_SCHEDULING_LANE_COUNT,
  );
}
