import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { createAuditChainStore } from '../../../libs/repositories/agent-harness/audit';
import {
  inDurableTransaction,
  type StoreSession,
} from '../../../libs/repositories/agent-harness/durable-transaction';
import type { AuditChainStore } from '../../agent-harness/ports/audit';
import {
  type SlackIdentityChange,
  slackIdentityAuditEntry,
} from '../../agent-harness/domain/slack-identity-audit';

/** Records who acts in PipesHub as a Slack person, on the organisation's audit chain (R38IDENT-FU-5). */
export type SlackIdentityAudit = (change: SlackIdentityChange) => Promise<void>;

/** For a link store that is not the deployment's (tests of the link rules alone). */
export const unauditedSlackIdentity: SlackIdentityAudit = () =>
  Promise.resolve();

let deploymentChain: Pick<AuditChainStore<StoreSession>, 'append'> | undefined;
const defaultChain = (): Pick<AuditChainStore<StoreSession>, 'append'> =>
  (deploymentChain ??= createAuditChainStore());

/**
 * Links and unlinks on the org's audit chain, as workspace downloads are (`ChainWorkspaceDownloadAudit`): one entry
 * in a transaction of its own, resolved once it committed. The organisation is the one the server resolved for the
 * bot or the admin, never one a request named.
 */
export function chainSlackIdentityAudit(
  chain: () => Pick<AuditChainStore<StoreSession>, 'append'> = defaultChain,
  transaction: <T>(
    work: (tx: StoreSession) => Promise<T>,
  ) => Promise<T> = inDurableTransaction,
  now: () => Date = () => new Date(),
): SlackIdentityAudit {
  return (change) =>
    forEnumeratedOrg(change.orgId, 'slack-identity-audit', async () => {
      const at = now();
      const entry = slackIdentityAuditEntry(change, at.getTime());
      await transaction((tx) =>
        chain().append(change.orgId, [entry], at.toISOString(), tx),
      );
    });
}
