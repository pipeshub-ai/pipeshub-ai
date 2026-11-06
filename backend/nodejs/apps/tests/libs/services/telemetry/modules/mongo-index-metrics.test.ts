import { expect } from 'chai';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';
import {
  refreshMongoMissingIndexes,
  setMongoMissingIndexes,
} from '../../../../../src/libs/services/telemetry/modules/mongo-index-metrics';

describe('telemetry modules/mongo-index-metrics', () => {
  const saved = process.env.TELEMETRY_COLLECTION_FLAG;
  beforeEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
  });
  afterEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = saved;
    metricsBackend.reset();
  });

  it('a refresh records the missing indexes counted at startup again, after the metrics were cleared', async () => {
    setMongoMissingIndexes(18);
    metricsBackend.reset();
    expect(await metricsBackend.serialize()).not.to.match(/^pipeshub_mongo_missing_indexes 18$/m);

    refreshMongoMissingIndexes();
    expect(await metricsBackend.serialize()).to.match(/^pipeshub_mongo_missing_indexes 18$/m);
  });
});
