import type { AuditAnchorSink } from '../../../../modules/agent-harness/ports/audit';
import type { AuditAnchorLookup } from '../../../../modules/agent-harness/ports/audit-queries';
import { pickHarnessStore } from '../stores';
import { MongoAuditAnchorSink } from './mongo';
import { PgAuditAnchorSink } from './pg';

export const createAuditAnchorSink = (): AuditAnchorSink & AuditAnchorLookup =>
  pickHarnessStore<AuditAnchorSink & AuditAnchorLookup>(
    'agent-harness/audit-anchor-sink',
    {
      mongo: () => new MongoAuditAnchorSink(),
      postgres: () => new PgAuditAnchorSink(),
    },
  );
