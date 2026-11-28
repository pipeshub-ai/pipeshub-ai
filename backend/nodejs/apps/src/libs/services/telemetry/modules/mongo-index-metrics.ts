import { metricsBackend } from '../metrics-backend';

const missingIndexes = metricsBackend.createGauge({
  name: 'pipeshub_mongo_missing_indexes',
  help: 'Schema indexes missing from MongoDB at startup while MONGO_AUTO_INDEX is false',
  labelNames: [],
});

let counted: number | null = null;

export function setMongoMissingIndexes(count: number): void {
  counted = count;
  missingIndexes.set({}, count);
}

/**
 * Sets the gauge again from the startup count. That count may be taken before the telemetry service has read whether
 * anything may be recorded, and a gauge set while nothing was recorded would stay at 0 for good.
 */
export function refreshMongoMissingIndexes(): void {
  if (counted !== null) missingIndexes.set({}, counted);
}
