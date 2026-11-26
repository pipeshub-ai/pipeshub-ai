import { SessionRole } from '../domain/session-roles';
import { SessionExecutionScope } from './chat-session-repository';

/**
 * The role `scope.actorId` holds in the session `scope` names, or null when
 * they are not a participant (or there is no such session in the org). Read
 * from the session store, never from the request.
 */
export interface SessionRoleLookup {
  roleOf(scope: SessionExecutionScope): Promise<SessionRole | null>;
  /**
   * Whether anyone besides the owner may read the session (SIGNIN: a person's
   * own plan is never used there). A session it cannot find reads as shared.
   */
  isShared?(scope: { orgId: string; sessionId: string }): Promise<boolean>;
}
