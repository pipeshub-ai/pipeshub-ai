import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { ScopedSession } from '../../../../authz/ports';
import { toCollaborator } from '../domain/collaborator.mapper';
import { orgWideTeamId } from '../principals/principal-resolver';
import { TEAM_EXPANSION_MAX_MEMBERS } from '../notify/recipient-resolver';
import { IAgentDirectory } from './agent.directory';
import { AGENT_MENTIONS_MAX, agentMentionVerdict } from './agent-mention-scope';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import {
  MentionDirectoryUnavailableError,
  MentionNotAllowedError,
  MentionSaAgentSharedError,
  TooManyAgentMentionsError,
} from './mention.errors';
import {
  ASSISTANT_MENTION_ID,
  MentionRef,
  dedupeMentions,
} from './mention.types';

export interface MentionValidatorDeps {
  users: IUserDirectory;
  teams: ITeamDirectory;
  agents: IAgentDirectory;
  /** With the agent builder flag on, any agent the sender may run is mentionable (guest turns); absent or off, only the chat's own agent. */
  flags?: IFeatureFlags;
}

export interface MentionValidationContext {
  readonly session: ScopedSession;
  readonly identity: CallerIdentity;
}

export interface ValidatedMentions {
  /** The client's tokens, de-duplicated; a non-participant stays in as a chip. */
  readonly mentions: readonly MentionRef[];
  /** Active org users mentioned who are not in the chat: never notified, offered to the owner as "add them". */
  readonly nonParticipants: readonly string[];
}

export interface IMentionValidator {
  /**
   * The client's tokens, checked against this chat and the sender. Throws on the first entry that
   * is not allowed (unknown, inactive, service or other-org user; foreign team; bad agent); an
   * active org colleague outside the chat is accepted and reported in `nonParticipants`.
   */
  validate(
    mentions: readonly MentionRef[],
    ctx: MentionValidationContext,
  ): Promise<ValidatedMentions>;
}

export interface Participants {
  readonly direct: ReadonlySet<string>;
  readonly teams: ReadonlySet<string>;
  readonly orgWide: boolean;
}

export function participantsOf(session: ScopedSession): Participants {
  const direct = new Set([session.userId.toString()]);
  const teams = new Set<string>();
  for (const row of session.sharedWith ?? []) {
    const c = toCollaborator(row);
    if (c?.principal.type === 'user') direct.add(c.principal.userId);
    if (c?.principal.type === 'team') teams.add(c.principal.teamId);
  }
  const wide = orgWideTeamId(session.orgId.toString());
  return {
    direct,
    teams: new Set([...teams].filter((t) => t !== wide)),
    orgWide: teams.has(wide),
  };
}

export class MentionValidator implements IMentionValidator {
  constructor(private readonly deps: MentionValidatorDeps) {}

  async validate(
    mentions: readonly MentionRef[],
    ctx: MentionValidationContext,
  ): Promise<ValidatedMentions> {
    const unique = dedupeMentions(mentions);
    const people = participantsOf(ctx.session);
    if (unique.filter((m) => m.type === 'agent').length > AGENT_MENTIONS_MAX) {
      throw new TooManyAgentMentionsError(AGENT_MENTIONS_MAX);
    }
    const guestAgents = await this.guestAgentsEnabled();
    const userIds: string[] = [];
    for (const [index, m] of unique.entries()) {
      if (m.type === 'user') userIds.push(m.id);
      else await this.checkOne(m, index, ctx, people, guestAgents);
    }
    const nonParticipants =
      userIds.length > 0 ? await this.checkUsers(unique, ctx, people) : [];
    return { mentions: unique, nonParticipants };
  }

  private async checkOne(
    m: MentionRef,
    index: number,
    ctx: MentionValidationContext,
    people: Participants,
    guestAgents: boolean,
  ): Promise<void> {
    if (m.type === 'assistant') {
      if (m.id !== ASSISTANT_MENTION_ID) {
        throw new MentionNotAllowedError(index, 'invalid_assistant');
      }
      return;
    }
    if (m.type === 'team') {
      if (!people.teams.has(m.id)) {
        throw new MentionNotAllowedError(index, 'not_a_chat_team');
      }
      return;
    }
    await this.checkAgent(m, index, ctx, guestAgents);
  }

  private async guestAgentsEnabled(): Promise<boolean> {
    const { flags } = this.deps;
    return flags ? flags.isEnabled(COLLAB_FLAG_KEYS.chatAgentBuilder) : false;
  }

  /** Only an agent in scope is mentionable, and only by someone who may run it. */
  private async checkAgent(
    m: MentionRef,
    index: number,
    ctx: MentionValidationContext,
    guestAgents: boolean,
  ): Promise<void> {
    const verdict = await agentMentionVerdict(
      ctx.session,
      ctx.identity,
      this.deps.agents,
      m.id,
      guestAgents,
    );
    if (verdict === 'not_in_scope')
      throw new MentionNotAllowedError(index, 'agent_not_in_chat', 403);
    if (verdict === 'unavailable') throw new MentionDirectoryUnavailableError();
    if (verdict === 'not_allowed')
      throw new MentionNotAllowedError(index, 'agent_not_allowed', 403);
    if (verdict === 'service_account') throw new MentionSaAgentSharedError(index);
  }

  private async checkUsers(
    mentions: readonly MentionRef[],
    ctx: MentionValidationContext,
    people: Participants,
  ): Promise<readonly string[]> {
    const orgId = ctx.session.orgId.toString();
    const ids = mentions.filter((m) => m.type === 'user').map((m) => m.id);
    const found = await this.deps.users.findByIds(orgId, ids);
    const active = new Set(
      found
        .filter((u) => u.kind === 'human' && !u.isDisabled)
        .map((u) => u.userId),
    );
    const viaTeams = await this.teamMembers(
      ids.filter((id) => active.has(id) && !people.direct.has(id)),
      ctx,
      people,
    );
    const outside: string[] = [];
    mentions.forEach((m, index) => {
      if (m.type !== 'user') return;
      if (!active.has(m.id)) {
        throw new MentionNotAllowedError(index, 'unknown_user');
      }
      if (!people.direct.has(m.id) && !people.orgWide && !viaTeams.has(m.id)) {
        outside.push(m.id);
      }
    });
    return outside;
  }

  /** Users among `ids` who reach the chat through a team principal; empty when none is needed. A team that cannot be expanded fails the send closed. */
  private async teamMembers(
    ids: readonly string[],
    ctx: MentionValidationContext,
    people: Participants,
  ): Promise<ReadonlySet<string>> {
    if (ids.length === 0 || people.orgWide || people.teams.size === 0) {
      return new Set();
    }
    const wanted = new Set(ids);
    const reached = new Set<string>();
    let unresolved = false;
    const results = await Promise.all(
      [...people.teams].map((teamId) =>
        this.deps.teams.memberUserIds(teamId, ctx.identity, {
          limit: TEAM_EXPANSION_MAX_MEMBERS,
        }),
      ),
    );
    for (const result of results) {
      if (result.status !== 'ok') unresolved = true;
      else
        result.userIds
          .filter((id) => wanted.has(id))
          .forEach((id) => reached.add(id));
    }
    if (unresolved && reached.size < wanted.size) {
      throw new MentionDirectoryUnavailableError();
    }
    return reached;
  }
}
