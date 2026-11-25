import type { ChatTitleReader } from '../../../../modules/agent-harness/ports/chat-titles';
import type {
  MemberProfile,
  OrgMemberProfiles,
} from '../../../../modules/agent-harness/ports/org-members';
import { getConversationRepository } from '../../conversation';
import { oidOf, oidsOf } from '../../pg-util';
import { getUserRepository } from '../../user';
import { inBoundOrgUnit } from '../pg-documents/ambient';
import { displayName } from './mongo';

/**
 * The names an admin page shows for user ids its org's records hold (AUDITUI), through main's user repository: the
 * org's own live users only, so a deleted user, or an id of another org, is not named.
 */
export class PgMemberProfiles implements OrgMemberProfiles {
  async profiles(
    orgId: string,
    userIds: readonly string[],
  ): Promise<Map<string, MemberProfile>> {
    const found = new Map<string, MemberProfile>();
    const ids = [...new Set(oidsOf([...userIds]))];
    if (ids.length === 0 || oidOf(orgId) === undefined) return found;
    const users = await inBoundOrgUnit(orgId, () =>
      getUserRepository().listLiveByIds(orgId, ids),
    );
    for (const user of users) {
      found.set(user._id, {
        name: displayName(user),
        email: user.email,
      });
    }
    return found;
  }
}

/** The titles of an org's chats, plain and agent alike, for an admin page (AUDITUI), through main's conversation repository. */
export class PgChatTitles implements ChatTitleReader {
  titles(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, string>> {
    if (oidOf(orgId) === undefined)
      return Promise.resolve(new Map<string, string>());
    return inBoundOrgUnit(orgId, () =>
      getConversationRepository().listTitles(orgId, sessionIds),
    );
  }
}
