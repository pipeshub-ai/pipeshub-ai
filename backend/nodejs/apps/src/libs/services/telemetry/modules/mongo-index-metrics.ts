import { metricsBackend } from '../metrics-backend';

const missingIndexes = metricsBackend.createGauge({
  name: 'pipeshub_mongo_missing_indexes',
  help: 'Schema indexes missing from MongoDB at startup while MONGO_AUTO_INDEX is false',
  labelNames: [],
});

export function setMongoMissingIndexes(count: number): void {
  missingIndexes.set({}, count);
}
