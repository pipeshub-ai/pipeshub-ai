import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ScopedSession } from '../../../../authz/ports';
import { isCollaborative } from '../http/resume-binding';
import { IAgentDirectory } from './agent.directory';

/** One agent per message; several in a row are a later phase. */
export const AGENT_MENTIONS_MAX = 1;

/**
 * Whether `agentKey` is nameable in this chat at all. With guest agents (agent builder flag) any
 * agent is, and the directory decides who may run it; without, only the chat's own agent is (M1).
 * The picker and the validator both go through this and `agentMentionVerdict`, so what is offered
 * is what is accepted.
 */
export const inAgentMentionScope = (
  session: { readonly agentKey?: string },
  guestAgents: boolean,
  agentKey: string,
): boolean => guestAgents || session.agentKey === agentKey;

export type AgentMentionVerdict =
  | 'allowed'
  | 'not_in_scope'
  | 'not_allowed'
  | 'service_account'
  | 'unavailable';

export async function agentMentionVerdict(
  session: ScopedSession,
  identity: CallerIdentity,
  agents: IAgentDirectory,
  agentKey: string,
  guestAgents: boolean,
): Promise<AgentMentionVerdict> {
  if (!inAgentMentionScope(session, guestAgents, agentKey)) {
    return 'not_in_scope';
  }
  const allowed = await agents.canExecute(identity, agentKey);
  if (allowed === 'unavailable') return 'unavailable';
  if (!allowed) return 'not_allowed';
  if (!isCollaborative(session)) return 'allowed';
  const serviceAccount = await agents.isServiceAccount(identity, agentKey);
  if (serviceAccount === 'unavailable') return 'unavailable';
  return serviceAccount ? 'service_account' : 'allowed';
}

/** The `candidates` the caller may mention; one the directory cannot vouch for is left out. */
export async function allowedAgentMentions(
  session: ScopedSession,
  identity: CallerIdentity,
  agents: IAgentDirectory,
  candidates: readonly string[],
  guestAgents: boolean,
): Promise<readonly string[]> {
  const verdicts = await Promise.all(
    candidates.map(
      async (key) =>
        [
          key,
          await agentMentionVerdict(session, identity, agents, key, guestAgents),
        ] as const,
    ),
  );
  return verdicts.filter(([, v]) => v === 'allowed').map(([key]) => key);
}
