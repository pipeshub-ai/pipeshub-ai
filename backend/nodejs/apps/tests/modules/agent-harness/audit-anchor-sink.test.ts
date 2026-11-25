/**
 * The AuditAnchorSink contract on `InMemoryAuditAnchorSink`; Mongo runs it in
 * audit-anchor-sink.mongo.itest.ts.
 */
import { InMemoryAuditAnchorSink } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { describeAuditAnchorSinkContract } from './support/audit-anchor-sink-contract';
import { describeAuditAnchorLookupContract } from './support/audit-anchor-lookup-contract';

describeAuditAnchorSinkContract('in-memory', async () => new InMemoryAuditAnchorSink());
describeAuditAnchorLookupContract('in-memory', async () => new InMemoryAuditAnchorSink());

