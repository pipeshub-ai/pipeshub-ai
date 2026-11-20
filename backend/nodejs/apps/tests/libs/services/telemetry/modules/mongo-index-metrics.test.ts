import { expect } from 'chai';
import * as flag from '../../../../../src/libs/services/telemetry/flag';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';
import {
  refreshMongoMissingIndexes,
  setMongoMissingIndexes,
} from '../../../../../src/libs/services/telemetry/modules/mongo-index-metrics';

describe('telemetry modules/mongo-index-metrics', () => {
  const saved = process.env.TELEMETRY_COLLECTION_FLAG;
  beforeEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = 'true';
    flag.setCollectionConsent(true);
  });
  afterEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = saved;
    metricsBackend.reset();
  });

  it('a refresh sets the gauge again from the count taken at startup', async () => {
    setMongoMissingIndexes(18);
    expect(await metricsBackend.serialize()).to.match(/^pipeshub_mongo_missing_indexes 18$/m);

    metricsBackend.reset();
    expect(await metricsBackend.serialize()).not.to.match(/^pipeshub_mongo_missing_indexes 18$/m);
    refreshMongoMissingIndexes();
    expect(await metricsBackend.serialize()).to.match(/^pipeshub_mongo_missing_indexes 18$/m);
  });
});
