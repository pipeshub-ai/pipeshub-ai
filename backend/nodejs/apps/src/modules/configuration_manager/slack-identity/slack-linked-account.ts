import { getUserRepository } from '../../../libs/repositories/user';
import { loadConfigurationManagerConfig } from '../config/config';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { getSlackUserLinkRepository } from '../../../libs/repositories/slack-user-link';
import {
  SlackIdentity,
  SlackIdentityLink,
  SlackIdentityLinks,
  SlackLinkKv,
} from './slack-identity-links';
import { chainSlackIdentityAudit } from './slack-identity-audit';

export interface LinkedAccount {
  orgId: string;
  userId: string;
  email: string;
  fullName?: string;
}

/** Only what liveness needs of the user repository, so tests can pass a fake. */
export interface LinkedAccountUsers {
  getLive(
    orgId: string,
    id: string,
  ): Promise<{
    _id: string;
    orgId: string;
    email: string;
    fullName?: string;
  } | null>;
  getAccountState(
    id: string,
  ): Promise<{ kind?: 'human' | 'service'; isDisabled?: boolean } | null>;
}

/**
 * The PipesHub account a link still names: live, in the org, not disabled and a person. A link whose account is
 * gone is removed here, so a disabled or deleted person's Slack identity stops working at once and stays unlinked
 * even if the account comes back (they link again). The removal is audited with why (R38IDENT-FU-5).
 */
export async function liveLinkedAccount(
  links: SlackIdentityLinks,
  identity: SlackIdentity,
  orgId: string,
  users: LinkedAccountUsers = getUserRepository(),
): Promise<LinkedAccount | null> {
  const link = await links.find(orgId, identity);
  if (!link) return null;
  const standing = await accountStanding(link, orgId, users);
  if ('account' in standing) return standing.account;
  await links.revoke(orgId, identity, {
    by: 'system',
    reason: standing.reason,
  });
  return null;
}

export type LinkedAccountStanding =
  | { account: LinkedAccount }
  | { reason: 'account_removed' | 'account_disabled' | 'service_account' };

/** The account a link names, or why it can no longer act. */
export async function accountStanding(
  link: SlackIdentityLink,
  orgId: string,
  users: LinkedAccountUsers = getUserRepository(),
): Promise<LinkedAccountStanding> {
  if (link.orgId !== orgId) return { reason: 'account_removed' };
  const user = await users.getLive(orgId, link.userId);
  if (!user) return { reason: 'account_removed' };
  const state = await users.getAccountState(link.userId);
  if (!state) return { reason: 'account_removed' };
  if (state.isDisabled === true) return { reason: 'account_disabled' };
  if (state.kind === 'service') return { reason: 'service_account' };
  return {
    account: {
      orgId: user.orgId,
      userId: user._id,
      email: user.email,
      fullName: user.fullName,
    },
  };
}

export async function accountOf(
  link: SlackIdentityLink,
  orgId: string,
  users: LinkedAccountUsers = getUserRepository(),
): Promise<LinkedAccount | null> {
  const standing = await accountStanding(link, orgId, users);
  return 'account' in standing ? standing.account : null;
}

let shared: SlackIdentityLinks | undefined;

/** The deployment's links, over the process's KV store. */
export function slackIdentityLinks(kv?: SlackLinkKv): SlackIdentityLinks {
  if (kv)
    return new SlackIdentityLinks(
      kv,
      getSlackUserLinkRepository(),
      Date.now,
      chainSlackIdentityAudit(),
    );
  shared ??= new SlackIdentityLinks(
    KeyValueStoreService.getInstance(loadConfigurationManagerConfig()),
    getSlackUserLinkRepository(),
    Date.now,
    chainSlackIdentityAudit(),
  );
  return shared;
}

export function setSlackIdentityLinksForTests(
  links: SlackIdentityLinks | undefined,
): void {
  shared = links;
}
