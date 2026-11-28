/**
 * The AuditAnchorSink contract on `MongoAuditAnchorSink` on a replica set
 * (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { describeAuditAnchorSinkContract } from './support/audit-anchor-sink-contract';
import { describeAuditAnchorLookupContract } from './support/audit-anchor-lookup-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  resetHarnessMongo,
} from './support/harness-mongo';

describe('AuditAnchorSink on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const mongo = async () => {
    await resetHarnessMongo();
    return new MongoAuditAnchorSink();
  };
  describeAuditAnchorSinkContract('mongo replica set', mongo);
  describeAuditAnchorLookupContract('mongo replica set', mongo);
});
