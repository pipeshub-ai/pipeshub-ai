import { DocStoreNotReady, mongoFallbacks } from './store-select';
import { Logger } from '../services/logger.service';
import { getActivityRepository } from './activity';
import { getConnectorsConfigRepository } from './connectors-config';
import { getConversationRepository } from './conversation';
import { getCredentialRepository } from './credential';
import { getDelegationRepository } from './delegation';
import { getDocumentRepository } from './document';
import { getGroupRepository } from './group';
import { getIdentityPort } from './identity';
import { getIdentityLookup } from './identity-lookup';
import { getMailLogRepository } from './mail-log';
import { getNotificationRepository } from './notification';
import { getOAuthAppRepository } from './oauth-app';
import { getOAuthTokenRepository } from './oauth-token';
import { getOrgRepository } from './org';
import { getOrgAuthConfigRepository } from './org-auth-config';
import { getOrgMetricsJobRepository } from './org-metrics-job';
import { getOutboxRepository } from './outbox';
import { getPictureRepository } from './picture';
import { getProjectRepository } from './project';
import { getResetLinkRepository } from './reset-link';
import { getSemanticSearchRepository } from './semantic-search';
import { getSlackThreadRepository } from './slack-thread';
import { getUserRepository } from './user';

/** Every repository, by directory. A test keeps this list equal to the directories. */
export const REPOSITORIES: Record<string, () => unknown> = {
  activity: getActivityRepository,
  'connectors-config': getConnectorsConfigRepository,
  conversation: getConversationRepository,
  credential: getCredentialRepository,
  delegation: getDelegationRepository,
  document: getDocumentRepository,
  group: getGroupRepository,
  identity: getIdentityPort,
  'identity-lookup': getIdentityLookup,
  'mail-log': getMailLogRepository,
  notification: getNotificationRepository,
  'oauth-app': getOAuthAppRepository,
  'oauth-token': getOAuthTokenRepository,
  org: getOrgRepository,
  'org-auth-config': getOrgAuthConfigRepository,
  'org-metrics-job': getOrgMetricsJobRepository,
  outbox: getOutboxRepository,
  picture: getPictureRepository,
  project: getProjectRepository,
  'reset-link': getResetLinkRepository,
  'semantic-search': getSemanticSearchRepository,
  'slack-thread': getSlackThreadRepository,
  user: getUserRepository,
};

/** Under `DOC_STORE=postgres`: names every repository that has no PostgreSQL implementation yet. Throws if any. */
export function assertAllRepositoriesReady(): void {
  const missing: string[] = [];
  for (const [name, get] of Object.entries(REPOSITORIES)) {
    try {
      get();
    } catch (e) {
      if (!(e instanceof DocStoreNotReady)) throw e;
      missing.push(name);
    }
  }
  if (missing.length)
    throw new Error(
      `DOC_STORE=postgres, but these repositories have no PostgreSQL implementation yet: ${missing.join(', ')}`,
    );
  if (mongoFallbacks.size)
    Logger.getInstance({ service: 'DocStore' }).error(
      'MIXED STORE (DOC_STORE_MONGO_REPOS): these repositories are read and written on Mongo, not PostgreSQL. For testing only.',
      { repositories: [...mongoFallbacks].sort() },
    );
}
