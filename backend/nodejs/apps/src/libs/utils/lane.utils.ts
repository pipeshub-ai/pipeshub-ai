import { createHash } from 'crypto';

import {
  ENV_FAIR_SCHEDULING_LANE_COUNT,
  ENV_MESSAGE_BROKER,
} from '../constants/messaging.constants';
import { MessageBrokerType } from '../types/messaging.types';
import { parsePositiveIntSafe } from './env.utils';

/**
 * Fair-scheduling lanes, producer side.
 *
 * A lane keeps one connector's backlog from sitting in front of another's.
 * How that is expressed differs by broker: on Kafka a lane is a partition and
 * the message key is enough for the broker's partitioner to place it, but
 * Redis Streams have no partitions and no key-based placement -- the key is
 * stored as a plain field -- so the lane has to be the stream name itself.
 * Publishing a record event to the base stream on Redis would therefore
 * bypass lane scheduling entirely.
 */

/** Lane count for the indexing topic; 1 disables laning. */
export function laneCount(): number {
  return parsePositiveIntSafe(
    process.env[ENV_FAIR_SCHEDULING_LANE_COUNT],
    1,
    ENV_FAIR_SCHEDULING_LANE_COUNT,
  );
}

export function isRedisBroker(): boolean {
  return (
    (process.env[ENV_MESSAGE_BROKER] ?? '').trim().toLowerCase() ===
    MessageBrokerType.REDIS
  );
}

/**
 * Map a fairness key to a lane.
 *
 * Must agree exactly with `stable_lane` in
 * backend/python/app/services/messaging/lanes/hash_router.py -- Python
 * publishes most record events and this service publishes some, so a
 * disagreement would put one connector on two different lanes depending on
 * which service produced the event.
 *
 * SHA-256 is what makes that agreement possible: Node's crypto cannot produce
 * BLAKE2b at an 8-byte digest (the digest length is mixed into BLAKE2b's IV,
 * so truncating blake2b512 gives a different value), while both runtimes
 * compute SHA-256 natively.
 */
export function stableLane(laneKey: string, lanes: number): number {
  if (lanes <= 1) return 0;
  const digest = createHash('sha256').update(laneKey, 'utf8').digest();
  return Number(digest.readBigUInt64BE(0) % BigInt(lanes));
}

/**
 * The stream a record event belongs on, or the topic unchanged when laning
 * does not apply (Kafka, or a single lane).
 */
export function laneStreamFor(topic: string, laneKey: string): string {
  if (!isRedisBroker()) return topic;
  const lanes = laneCount();
  if (lanes <= 1) return topic;
  return `${topic}.${stableLane(laneKey, lanes)}`;
}
