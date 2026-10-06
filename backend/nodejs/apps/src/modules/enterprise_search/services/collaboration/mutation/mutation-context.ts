import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { IScopedChatLoader, ScopedSession } from '../../../../authz/ports';
import { ConversationRef } from '../domain/types';
import { ConversationAccessGrant } from '../http/conversation-context';
import { CollaborationEvent } from '../notify/events';
import { SessionScope } from '../persistence/collaborator.repository';
import { AuditContext } from './audit-events';

const isAgent = (
  session: ScopedSession,
): session is ScopedSession & { agentKey: string } =>
  (session.agentKey ?? '') !== '';

export const refOf = (session: ScopedSession): ConversationRef =>
  isAgent(session)
    ? {
        kind: 'agent',
        conversationId: session._id.toString(),
        agentKey: session.agentKey,
      }
    : { kind: 'chat', conversationId: session._id.toString() };

/** Fields every event shares; the caller is the actor. */
export const eventBaseOf = (
  grant: ConversationAccessGrant,
): Pick<CollaborationEvent, 'orgId' | 'sessionId' | 'ref' | 'actorUserId'> => ({
  orgId: grant.caller.orgId,
  sessionId: grant.session._id.toString(),
  ref: refOf(grant.session),
  actorUserId: grant.caller.userId,
});

export const auditContextOf = (
  grant: ConversationAccessGrant,
  identity: CallerIdentity,
): AuditContext => ({
  orgId: grant.caller.orgId,
  actorUserId: grant.caller.userId,
  sessionId: grant.session._id.toString(),
  requestId: (identity.requestKey as { context?: { requestId?: string } })
    .context?.requestId,
});

/** Owner-gated unless `ownerGated` is false, which is an editor's invite the service has already authorised. */
export const scopeOf = (
  grant: ConversationAccessGrant,
  ownerGated: boolean,
): SessionScope => ({
  sessionId: grant.session._id.toString(),
  orgId: grant.caller.orgId,
  ...(ownerGated && { ownerId: grant.caller.userId }),
});

/** How `IScopedChatLoader` finds this conversation again after a write. */
export const loadTargetOf = (
  session: ScopedSession,
): Parameters<IScopedChatLoader['loadScoped']>[1] =>
  isAgent(session)
    ? { id: session._id.toString(), kind: 'agent', agentKey: session.agentKey }
    : { id: session._id.toString(), kind: 'chat' };
