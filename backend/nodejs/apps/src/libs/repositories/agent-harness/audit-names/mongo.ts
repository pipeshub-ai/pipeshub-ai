import mongoose from 'mongoose';
import type { ChatTitleReader } from '../../../../modules/agent-harness/ports/chat-titles';
import type {
  MemberProfile,
  OrgMemberProfiles,
} from '../../../../modules/agent-harness/ports/org-members';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';

const objectIds = (ids: readonly string[]): string[] => [
  ...new Set(ids.filter((id) => mongoose.isValidObjectId(id))),
];

export interface UserNameFields {
  _id: unknown;
  fullName?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}

export const displayName = (user: UserNameFields): string => {
  const full = user.fullName?.trim() ?? '';
  if (full !== '') return full;
  const parts = [user.firstName, user.lastName]
    .map((part) => part?.trim() ?? '')
    .filter((part) => part !== '')
    .join(' ');
  return parts !== '' ? parts : (user.email ?? '');
};

/**
 * The names an admin page shows for user ids its org's records hold
 * (AUDITUI): the org's own live users only, so a deleted user, or an id of
 * another org, is not named.
 */
export class HarnessMemberProfiles implements OrgMemberProfiles {
  async profiles(
    orgId: string,
    userIds: readonly string[],
  ): Promise<Map<string, MemberProfile>> {
    const ids = objectIds(userIds);
    const found = new Map<string, MemberProfile>();
    if (ids.length === 0 || !mongoose.isValidObjectId(orgId)) return found;
    const users = await Users.find({
      _id: { $in: ids },
      orgId,
      isDeleted: { $ne: true },
    })
      .select('fullName firstName lastName email')
      .lean<UserNameFields[]>();
    for (const user of users) {
      found.set(String(user._id), {
        name: displayName(user),
        email: user.email ?? '',
      });
    }
    return found;
  }
}

/**
 * The titles of an org's chats, plain and agent alike (one collection), for
 * an admin page (AUDITUI). A deleted chat is absent, as an erased one is.
 */
export class HarnessChatTitles implements ChatTitleReader {
  async titles(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<Map<string, string>> {
    const ids = objectIds(sessionIds);
    const found = new Map<string, string>();
    if (ids.length === 0 || !mongoose.isValidObjectId(orgId)) return found;
    const sessions = await ChatSession.find({
      _id: { $in: ids },
      orgId,
      isDeleted: false,
    })
      .select('title')
      .lean<Array<{ _id: unknown; title?: string }>>();
    for (const session of sessions) {
      found.set(String(session._id), session.title ?? '');
    }
    return found;
  }
}
