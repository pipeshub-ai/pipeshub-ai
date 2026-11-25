import { WorkerFeature } from '../domain/worker-compat';
import { StoreClock, systemStoreClock } from '../domain/execution';
import {
  FeatureSighting,
  WorkerFeatureSightings,
} from '../ports/worker-features';

export class InMemoryWorkerFeatureSightings implements WorkerFeatureSightings {
  private readonly seen = new Map<WorkerFeature, number>();

  constructor(private readonly clock: StoreClock = systemStoreClock) {}

  saw(features: readonly WorkerFeature[]): Promise<void> {
    const now = this.clock.now();
    for (const feature of features) {
      this.seen.set(feature, Math.max(this.seen.get(feature) ?? 0, now));
    }
    return Promise.resolve();
  }

  sighting(feature: WorkerFeature): Promise<FeatureSighting> {
    return Promise.resolve({
      seenAt: this.seen.get(feature) ?? null,
      now: this.clock.now(),
    });
  }
}
