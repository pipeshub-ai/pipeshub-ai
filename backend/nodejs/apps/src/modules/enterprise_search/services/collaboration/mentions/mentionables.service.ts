import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import {
  ITeamLookup,
  TeamInfo,
} from '../../../../user_management/services/team-lookup.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { toCollaborator } from '../domain/collaborator.mapper';
import { ConversationAccessGrant } from '../http/conversation-context';
import { orgWideTeamId } from '../principals/principal-resolver';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { AgentProfile, IAgentDirectory, IAgentProfiles } from './agent.directory';
import { allowedAgentMentions } from './agent-mention-scope';
import { ASSISTANT_MENTION_ID, MentionType } from './mention.types';

export interface Mentionable {
  readonly type: MentionType;
  readonly id: string;
  readonly label: string;
  /** An agent's `@handle`, without the `@`. */
  readonly handle?: string;
}

export interface MentionablesQuery {
  readonly q: string;
  readonly limit: number;
}

export interface IMentionablesService {
  list(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    query: MentionablesQuery,
  ): Promise<{ items: readonly Mentionable[] }>;
}

export const ASSISTANT_LABEL = 'PipesHub';

const matchesHandle = (handle: string | undefined, q: string): boolean =>
  handle !== undefined && q !== '' && handle.startsWith(q.replace(/^@/, ''));

const matches = (label: string, q: string): boolean =>
  q === '' ||
  label
    .toLowerCase()
    .split(/\s+/u)
    .some((word) => word.startsWith(q));

/**
 * What the composer may offer: the assistant, the chat's people and the teams it is shared with
 * (only the owner for a caller who may not see the member list).
 * Everything comes from this chat's own rows, filtered to the caller's org, so no other org's
 * people or teams can appear.
 */
export class MentionablesService implements IMentionablesService {
  constructor(
    private readonly users: IUserDirectory,
    private readonly teams: ITeamLookup,
    /** Agents are offered only when both this and the flags are given and the agent builder is on. */
    private readonly agentDirectory?: IAgentDirectory & IAgentProfiles,
    private readonly flags?: IFeatureFlags,
  ) {}

  async list(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    query: MentionablesQuery,
  ): Promise<{ items: readonly Mentionable[] }> {
    const { session, caller } = grant;
    const q = query.q.trim().toLowerCase();
    const userIds = new Set([session.userId.toString()]);
    const teamIds = new Set<string>();
    // I-9: who else is in the chat is for the owner and an inviting editor; others see the owner only.
    const rows = grant.view.canInvite ? (session.sharedWith ?? []) : [];
    for (const row of rows) {
      const c = toCollaborator(row);
      if (c?.principal.type === 'user') userIds.add(c.principal.userId);
      if (c?.principal.type === 'team') teamIds.add(c.principal.teamId);
    }
    userIds.delete(caller.userId);
    teamIds.delete(orgWideTeamId(caller.orgId));

    const [found, teamInfo, agents] = await Promise.all([
      this.users.findByIds(caller.orgId, [...userIds]),
      teamIds.size > 0
        ? this.teams.describeMany([...teamIds], identity)
        : Promise.resolve(new Map<string, TeamInfo>()),
      this.agentsFor(session, identity),
    ]);
    const items: Mentionable[] = [];
    if (matches(ASSISTANT_LABEL, q)) {
      items.push({
        type: 'assistant',
        id: ASSISTANT_MENTION_ID,
        label: ASSISTANT_LABEL,
      });
    }
    items.push(
      ...agents
        .filter((a) => matches(a.name, q) || matchesHandle(a.handle, q))
        .map((a) => ({
          type: 'agent' as const,
          id: a.agentKey,
          label: a.name,
          ...(a.handle !== undefined && { handle: a.handle }),
        })),
    );
    items.push(
      ...found
        .filter(
          (u) => u.kind === 'human' && !u.isDisabled && u.displayName !== '',
        )
        .filter((u) => matches(u.displayName, q))
        .sort((a, b) => a.displayName.localeCompare(b.displayName))
        .map((u) => ({
          type: 'user' as const,
          id: u.userId,
          label: u.displayName,
        })),
    );
    for (const [id, info] of teamInfo) {
      if (info.status === 'ok' && matches(info.name, q)) {
        items.push({ type: 'team', id, label: info.name });
      }
    }
    return { items: items.slice(0, query.limit) };
  }

  /** What the validator would accept for the caller, labelled for the picker. */
  private async agentsFor(
    session: ConversationAccessGrant['session'],
    identity: CallerIdentity,
  ): Promise<readonly (AgentProfile & { agentKey: string })[]> {
    const directory = this.agentDirectory;
    if (
      !directory ||
      !this.flags ||
      !(await this.flags.isEnabled(COLLAB_FLAG_KEYS.chatAgentBuilder))
    ) {
      return [];
    }
    const keys = await allowedAgentMentions(session, identity, directory);
    const profiles = await Promise.all(
      keys.map(async (agentKey) => {
        const p = await directory.describe(identity, agentKey);
        return p ? [{ agentKey, ...p }] : [];
      }),
    );
    return profiles.flat();
  }
}
