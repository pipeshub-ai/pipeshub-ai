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
  });
  afterEach(() => {
    process.env.TELEMETRY_COLLECTION_FLAG = saved;
    flag.setCollectionConsent(true);
    metricsBackend.reset();
  });

  it('counts the missing indexes found at startup once Data Collection is on, though they were counted before', async () => {
    metricsBackend.reset();
    flag.setCollectionConsent(false);
    setMongoMissingIndexes(18);
    expect(await metricsBackend.serialize()).not.to.match(/^pipeshub_mongo_missing_indexes 18$/m);

    flag.setCollectionConsent(true);
    refreshMongoMissingIndexes();
    expect(await metricsBackend.serialize()).to.match(/^pipeshub_mongo_missing_indexes 18$/m);
  });

  it('records nothing on a refresh while Data Collection is off', async () => {
    setMongoMissingIndexes(3);
    metricsBackend.reset();
    flag.setCollectionConsent(false);
    refreshMongoMissingIndexes();
    expect(await metricsBackend.serialize()).not.to.match(/^pipeshub_mongo_missing_indexes 3$/m);
  });
});
