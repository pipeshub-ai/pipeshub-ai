import { getConversationRepository } from '../../conversation';
import {
  SessionRole,
  sessionRoleOf,
} from '../../../../modules/agent-harness/domain/session-roles';
import type { HarnessSessionRow } from '../../conversation/types';
import type { SessionExecutionScope } from '../../../../modules/agent-harness/ports/chat-session-repository';
import type { SessionRoleLookup } from '../../../../modules/agent-harness/ports/session-roles';
import { hasValidIds, sessionIs } from '../chat-session-repository/pg';
import { inBoundOrgUnit } from '../durable-transaction/bound-org';

const read = (
  orgId: string,
  sessionId: string,
): Promise<HarnessSessionRow | null> =>
  inBoundOrgUnit(orgId, () =>
    getConversationRepository().findHarnessSession(sessionId, orgId),
  );
import { sessionParticipantsOf } from './mongo';

/** Roles read from the conversation (chat or agent, legacy or durable) through the conversation repository. */
export class PgHarnessSessionRoles implements SessionRoleLookup {
  async roleOf(scope: SessionExecutionScope): Promise<SessionRole | null> {
    if (!hasValidIds(scope.sessionId, scope.orgId, scope.actorId)) return null;
    const row = await read(scope.orgId, scope.sessionId);
    if (
      row === null ||
      !sessionIs.live(row) ||
      !sessionIs.ofKind(row, scope.agentKey)
    ) {
      return null;
    }
    return sessionRoleOf(sessionParticipantsOf(row.session), scope.actorId);
  }

  async isShared(scope: {
    orgId: string;
    sessionId: string;
  }): Promise<boolean> {
    if (!hasValidIds(scope.sessionId, scope.orgId)) return true;
    const row = await read(scope.orgId, scope.sessionId);
    if (row === null || !sessionIs.live(row)) return true;
    const participants = sessionParticipantsOf(row.session);
    return participants.isShared && participants.shares.length > 0;
  }
}
