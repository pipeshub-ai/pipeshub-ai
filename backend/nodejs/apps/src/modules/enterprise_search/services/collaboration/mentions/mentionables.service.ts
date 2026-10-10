import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import {
  ITeamLookup,
  TeamInfo,
} from '../../../../user_management/services/team-lookup.service';
import {
  DirectoryUser,
  IUserDirectory,
  matchesSearchTokens,
  searchTokens,
} from '../../../../user_management/services/user-directory.service';
import { toCollaborator } from '../domain/collaborator.mapper';
import { ConversationAccessGrant } from '../http/conversation-context';
import { orgWideTeamId } from '../principals/principal-resolver';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { IAgentDirectory, IAgentListing, ListedAgent } from './agent.directory';
import { allowedAgentMentions } from './agent-mention-scope';
import { ASSISTANT_MENTION_ID, MentionType } from './mention.types';

export interface Mentionable {
  readonly type: MentionType;
  readonly id: string;
  readonly label: string;
  /** An agent's `@handle`, without the `@`. */
  readonly handle?: string;
  /** Users only: whether they are in this chat. A colleague who is not can still be mentioned; the sender is offered to add them. */
  readonly inChat?: boolean;
  readonly email?: string;
}

export interface MentionablesQuery {
  readonly q: string;
  readonly limit: number;
}

export interface IMentionablesService {
  listForNewChat(
    identity: CallerIdentity,
    query: MentionablesQuery & { include: readonly string[]; agentKey?: string },
  ): Promise<{ items: readonly Mentionable[] }>;
  list(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    query: MentionablesQuery,
  ): Promise<{ items: readonly Mentionable[] }>;
}

export const ASSISTANT_LABEL = 'PipesHub';
/** Agents offered in the @ picker at most. */
export const AGENT_PICKER_MAX = 20;

const matchesHandle = (handle: string | undefined, q: string): boolean =>
  handle !== undefined && q !== '' && handle.startsWith(q.replace(/^@/, ''));

const isMentionableHuman = (u: DirectoryUser): boolean =>
  u.kind === 'human' && !u.isDisabled && (u.displayName !== '' || !!u.email);

const toUserItem = (u: DirectoryUser, inChat: boolean): Mentionable => ({
  type: 'user',
  id: u.userId,
  label: u.displayName !== '' ? u.displayName : (u.email ?? ''),
  inChat,
  ...(u.email !== undefined && { email: u.email }),
});

const startsWithFirstName = (u: DirectoryUser, tokens: readonly string[]): boolean =>
  tokens.length > 0 &&
  (u.firstName ?? u.displayName.split(/\s+/u)[0] ?? '')
    .toLowerCase()
    .startsWith(tokens[0] as string);

/** In-chat people first, then first-name prefix matches, then alphabetical. */
function rankPeople<T extends { user: DirectoryUser; inChat: boolean }>(
  people: readonly T[],
  tokens: readonly string[],
): T[] {
  const label = (u: DirectoryUser): string =>
    u.displayName !== '' ? u.displayName : (u.email ?? '');
  return [...people].sort(
    (a, b) =>
      Number(b.inChat) - Number(a.inChat) ||
      Number(startsWithFirstName(b.user, tokens)) -
        Number(startsWithFirstName(a.user, tokens)) ||
      label(a.user).localeCompare(label(b.user)),
  );
}

const matches = (label: string, q: string): boolean =>
  q === '' ||
  label
    .toLowerCase()
    .split(/\s+/u)
    .some((word) => word.startsWith(q));

interface ComposeInput {
  q: string;
  tokens: readonly string[];
  limit: number;
  callerId: string;
  found: readonly DirectoryUser[];
  directory: readonly DirectoryUser[];
  teamInfo: ReadonlyMap<string, TeamInfo>;
  agents: readonly ListedAgent[];
}

function compose(input: ComposeInput): { items: readonly Mentionable[] } {
  const { q, tokens, limit, callerId, found, directory, teamInfo, agents } =
    input;
    const items: Mentionable[] = [];
    if (matches(ASSISTANT_LABEL, q)) {
      items.push({
        type: 'assistant',
        id: ASSISTANT_MENTION_ID,
        label: ASSISTANT_LABEL,
      });
    }
    items.push(
      ...agents.map((a) => ({
        type: 'agent' as const,
        id: a.agentKey,
        label: a.name,
        ...(a.handle !== undefined && { handle: a.handle }),
      })),
    );
    const inChat = found.filter((u) => matchesSearchTokens(u, tokens));
    const seen = new Set([callerId, ...inChat.map((u) => u.userId)]);
    const outside = directory.filter((u) => !seen.has(u.userId));
    items.push(
      ...rankPeople(
        [
          ...inChat.map((u) => ({ user: u, inChat: true })),
          ...outside.map((u) => ({ user: u, inChat: false })),
        ].filter(({ user }) => isMentionableHuman(user)),
        tokens,
      ).map(({ user, inChat: here }) => toUserItem(user, here)),
    );
    for (const [id, info] of teamInfo) {
      if (info.status === 'ok' && matches(info.name, q)) {
        items.push({ type: 'team', id, label: info.name });
      }
    }
    return { items: items.slice(0, limit) };
}

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
    private readonly agentDirectory?: IAgentDirectory & IAgentListing,
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

    const tokens = searchTokens(q);
    const [found, directory, teamInfo, agents] = await Promise.all([
      this.users.findByIds(caller.orgId, [...userIds]),
      // Only someone who may invite is offered colleagues outside the chat (I-9).
      grant.view.canInvite && tokens.length > 0
        ? this.users.searchOrgMembers(caller.orgId, q, query.limit)
        : Promise.resolve([] as readonly DirectoryUser[]),
      teamIds.size > 0
        ? this.teams.describeMany([...teamIds], identity)
        : Promise.resolve(new Map<string, TeamInfo>()),
      this.agentsFor(session, identity, q, query.limit),
    ]);
    return compose({
      q,
      tokens,
      limit: query.limit,
      callerId: caller.userId,
      found,
      directory,
      teamInfo,
      agents,
    });
  }

  /**
   * The picker for a chat that does not exist yet: the sender is its would-be owner, so the whole
   * org directory is theirs to search. `include` are the draft collaborators, who count as in the chat.
   */
  async listForNewChat(
    identity: CallerIdentity,
    query: MentionablesQuery & {
      include: readonly string[];
      agentKey?: string;
    },
  ): Promise<{ items: readonly Mentionable[] }> {
    const q = query.q.trim().toLowerCase();
    const tokens = searchTokens(q);
    const drafts = query.include.filter((id) => id !== identity.userId);
    const draftChat = {
      userId: identity.userId,
      orgId: identity.orgId,
      // Anyone invited makes the chat shared, which is when service-account agents drop out.
      sharedWith: drafts.map((userId) => ({ userId, accessLevel: 'read' })),
      ...(query.agentKey !== undefined && { agentKey: query.agentKey }),
    } as unknown as ConversationAccessGrant['session'];
    const [found, directory, agents] = await Promise.all([
      this.users.findByIds(identity.orgId, drafts),
      tokens.length > 0
        ? this.users.searchOrgMembers(identity.orgId, q, query.limit)
        : Promise.resolve([] as readonly DirectoryUser[]),
      this.agentsFor(draftChat, identity, q, query.limit),
    ]);
    return compose({
      q,
      tokens,
      limit: query.limit,
      callerId: identity.userId,
      found,
      directory,
      teamInfo: new Map<string, TeamInfo>(),
      agents,
    });
  }

  /** What the validator would accept for the caller, labelled for the picker: the agents they may run that match the typed prefix. */
  private async agentsFor(
    session: ConversationAccessGrant['session'],
    identity: CallerIdentity,
    q: string,
    limit: number,
  ): Promise<readonly ListedAgent[]> {
    const directory = this.agentDirectory;
    if (
      !directory ||
      !this.flags ||
      !(await this.flags.isEnabled(COLLAB_FLAG_KEYS.chatAgentBuilder))
    ) {
      return [];
    }
    const listed = await directory.listExecutable(identity);
    if (listed === 'unavailable') return [];
    const candidates = listed
      .filter((a) => matches(a.name, q) || matchesHandle(a.handle, q))
      .sort(
        (a, b) =>
          Number(b.agentKey === session.agentKey) -
          Number(a.agentKey === session.agentKey),
      )
      .slice(0, AGENT_PICKER_MAX * 2);
    const cap = Math.min(AGENT_PICKER_MAX, Math.max(5, Math.floor(limit / 2)));
    const allowed = new Set(
      await allowedAgentMentions(
        session,
        identity,
        directory,
        candidates.map((a) => a.agentKey),
        true,
      ),
    );
    return candidates
      .filter((a) => allowed.has(a.agentKey))
      .slice(0, cap);
  }
}
