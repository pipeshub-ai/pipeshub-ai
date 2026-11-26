import type { HarnessLogger } from '../../../../modules/agent-harness/application/harness-log';
import type { AuditChainStore } from '../../../../modules/agent-harness/ports/audit';
import type { AuditChainQueries } from '../../../../modules/agent-harness/ports/audit-queries';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import {
  auditLinksIdle as mongoAuditLinksIdle,
  MongoAuditChainStore,
} from './mongo';
import { PgAuditChainStore, pgAuditLinksIdle } from './pg';

export type HarnessAuditChain = AuditChainStore<Tx> &
  AuditChainQueries & { settle(orgId: string): Promise<void> };

export const createAuditChainStore = (
  logger?: HarnessLogger,
): HarnessAuditChain =>
  pickHarnessStore<HarnessAuditChain>('agent-harness/audit', {
    mongo: () => new MongoAuditChainStore(logger),
    postgres: () => new PgAuditChainStore(logger),
  });

/** No audit link is under way, queued or waiting in this process, on either store (shutdown, and tests). */
export const auditLinksIdle = async (): Promise<void> => {
  await mongoAuditLinksIdle();
  await pgAuditLinksIdle();
};
