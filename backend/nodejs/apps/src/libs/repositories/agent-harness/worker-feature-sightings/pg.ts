import { WorkerFeature } from '../../../../modules/agent-harness/domain/worker-compat';
import {
  FeatureSighting,
  WorkerFeatureSightings,
} from '../../../../modules/agent-harness/ports/worker-features';
import { deploymentUnit } from '../pg-documents/pg';
import { PgTime, pgServerTime } from '../time/pg';
import { featureLivenessId } from './ids';

/** One liveness record per feature beside the control plane's (`harness.liveness`), on database time. */
export class PgWorkerFeatureSightings implements WorkerFeatureSightings {
  constructor(private readonly time: PgTime = pgServerTime) {}

  async saw(features: readonly WorkerFeature[]): Promise<void> {
    if (features.length === 0) return;
    await deploymentUnit({ write: true }, async (k) =>
      k.query(
        `/* pipeshub:harness.liveness_saw */ INSERT INTO harness.liveness AS l (id, seen_at, previous_seen_at, holdoff_until)
           SELECT id, $2, NULL, 0 FROM unnest($1::text[]) AS f(id)
         ON CONFLICT (id) DO UPDATE SET seen_at = GREATEST(l.seen_at, excluded.seen_at)`,
        [[...new Set(features)].map(featureLivenessId), await this.time.now(k)],
      ),
    );
  }

  sighting(feature: WorkerFeature): Promise<FeatureSighting> {
    return deploymentUnit({ write: false }, async (k) => {
      const { rows } = await k.query<{ seen_at: number | null }>(
        `/* pipeshub:harness.liveness_sighting */ SELECT seen_at FROM harness.liveness WHERE id = $1`,
        [featureLivenessId(feature)],
      );
      const seenAt = rows[0]?.seen_at;
      return {
        seenAt: seenAt ?? null,
        now: await this.time.now(k),
      };
    });
  }
}
