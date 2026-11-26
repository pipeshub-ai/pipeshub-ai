import { forEnumeratedOrg } from '../../libs/context/orgContext';
import { createAuditChainStore } from '../../libs/repositories/agent-harness/audit';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../libs/repositories/agent-harness/durable-transaction';
import type { AuditChainStore } from '../agent-harness/ports/audit';
import {
  type SessionReplay,
  sessionReplayAuditEntry,
} from '../agent-harness/domain/session-replay-audit';

export type SessionReplayAudit = (replay: SessionReplay) => Promise<void>;

let deploymentChain: Pick<AuditChainStore<StoreSession>, 'append'> | undefined;
const defaultChain = (): Pick<AuditChainStore<StoreSession>, 'append'> =>
  (deploymentChain ??= createAuditChainStore());

/**
 * An ended sign-in on the org's audit chain, as Slack links are: one entry in a transaction of its own. The org is
 * the one in the verified refresh token, never one a request named.
 */
export function chainSessionReplayAudit(
  chain: () => Pick<AuditChainStore<StoreSession>, 'append'> = defaultChain,
  transaction: <T>(
    work: (tx: StoreSession) => Promise<T>,
  ) => Promise<T> = inDurableTransaction,
  now: () => Date = () => new Date(),
): SessionReplayAudit {
  return (replay) =>
    forEnumeratedOrg(replay.orgId, 'session-replay-audit', async () => {
      const at = now();
      const entry = sessionReplayAuditEntry(replay, at.getTime());
      await transaction((tx) =>
        chain().append(replay.orgId, [entry], at.toISOString(), tx),
      );
    });
}
