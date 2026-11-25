import { WorkerFeature } from '../../../../modules/agent-harness/domain/worker-compat';
import {
  FeatureSighting,
  WorkerFeatureSightings,
} from '../../../../modules/agent-harness/ports/worker-features';
import { AgentHarnessLivenessModel } from '../control-plane-liveness/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';

import { featureLivenessId } from './ids';

export { featureLivenessId };

/** On DB time, as control-plane liveness: a replica's skewed clock cannot fake a live worker. */
export class MongoWorkerFeatureSightings implements WorkerFeatureSightings {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async saw(features: readonly WorkerFeature[]): Promise<void> {
    if (features.length === 0) return;
    await AgentHarnessLivenessModel.bulkWrite(
      features.map((feature) => ({
        updateOne: {
          filter: { _id: featureLivenessId(feature) },
          update: [
            {
              $set: {
                seenAt: {
                  $max: [{ $ifNull: ['$seenAt', 0] }, this.time.expr()],
                },
                previousSeenAt: { $ifNull: ['$previousSeenAt', null] },
                holdoffUntil: { $ifNull: ['$holdoffUntil', 0] },
              },
            },
          ],
          upsert: true,
        },
      })),
      { ordered: false },
    );
  }

  async sighting(feature: WorkerFeature): Promise<FeatureSighting> {
    const [document, now] = await Promise.all([
      AgentHarnessLivenessModel.findById(featureLivenessId(feature)).lean(),
      this.time.read(),
    ]);
    return { seenAt: document?.seenAt ?? null, now };
  }
}
