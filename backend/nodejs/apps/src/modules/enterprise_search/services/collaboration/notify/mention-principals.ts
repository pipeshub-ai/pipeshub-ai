import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ScopedSession } from '../../../../authz/ports';
import { Principal } from '../domain/types';
import { participantsOf } from '../mentions/mention.validator';
import { MentionRef } from '../mentions/mention.types';
import { IRecipientResolver } from './recipient-resolver';

/**
 * The principals a mention may notify: users who can open the chat (owner, direct share, a chat team's
 * member, or anyone when the org is on it) and teams the chat is shared with. A mention never grants
 * access, so anything else is dropped here even if a client got it past validation.
 */
export async function mentionPrincipals(args: {
  mentions: readonly MentionRef[];
  session: ScopedSession;
  recipients: IRecipientResolver;
  identity: CallerIdentity;
}): Promise<Principal[]> {
  const { mentions, session, recipients, identity } = args;
  const people = participantsOf(session);
  const userIds = [
    ...new Set(mentions.filter((m) => m.type === 'user').map((m) => m.id)),
  ];
  const teamIds = [
    ...new Set(mentions.filter((m) => m.type === 'team').map((m) => m.id)),
  ].filter((id) => people.teams.has(id));
  const outside = userIds.filter((id) => !people.direct.has(id));
  let reachable = new Set<string>();
  if (outside.length > 0 && people.orgWide) {
    reachable = new Set(outside);
  } else if (outside.length > 0 && people.teams.size > 0) {
    const members = await recipients.resolve({
      orgId: session.orgId.toString(),
      identity,
      principals: [...people.teams].map((teamId) => ({
        type: 'team' as const,
        teamId,
      })),
    });
    reachable = new Set(members);
  }
  return [
    ...userIds
      .filter((id) => people.direct.has(id) || reachable.has(id))
      .map((userId) => ({ type: 'user' as const, userId })),
    ...teamIds.map((teamId) => ({ type: 'team' as const, teamId })),
  ];
}

/**
 * The only rows whose mentions notify: a person's question or note. The structured `mentions` field
 * is what counts; a `<@user:…>` token in text, in a bot answer, a tool row or an edit never does (MN-14).
 */
export function notifiableMentions(row: {
  messageType?: string;
  mentions?: readonly MentionRef[];
}): readonly MentionRef[] {
  return row.messageType === 'user_query' || row.messageType === 'note'
    ? (row.mentions ?? []).map(({ type, id }) => ({ type, id }))
    : [];
}
