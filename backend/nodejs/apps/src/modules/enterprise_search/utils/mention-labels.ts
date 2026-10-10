import { Logger } from '../../../libs/services/logger.service';
import { CallerIdentity } from '../../../libs/types/caller-identity';
import { MentionRef, mentionKey } from '../services/collaboration/mentions/mention.types';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';

const logger = Logger.getInstance({ service: 'Mention labels' });

type LabelDeps = Pick<ConversationTurnDeps, 'users' | 'agents' | 'teamLookup'>;

/**
 * Display names for the user, team and agent mentions of a first message, keyed `type:id`, for the
 * chat title. A mention that cannot be named is left out and its token is dropped from the title;
 * a failed lookup never fails the send.
 */
export async function mentionLabels(
  deps: LabelDeps,
  identity: CallerIdentity | undefined,
  mentions: readonly MentionRef[],
): Promise<ReadonlyMap<string, string>> {
  const labels = new Map<string, string>();
  if (!identity || mentions.length === 0) return labels;
  const ofType = (type: MentionRef['type']): string[] =>
    mentions.filter((m) => m.type === type).map((m) => m.id);
  const userIds = ofType('user');
  const teamIds = ofType('team');
  const agentIds = ofType('agent');
  const set = (type: MentionRef['type'], id: string, name?: string): void => {
    if (name?.trim()) labels.set(mentionKey({ type, id }), name.trim());
  };
  const attempt = async (what: string, run: () => Promise<void>): Promise<void> => {
    try {
      await run();
    } catch (error) {
      logger.warn('Mention label lookup failed; title omits them', {
        what,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  };
  await Promise.all([
    userIds.length > 0 &&
      attempt('users', async () => {
        const names = await deps.users.displayNames(identity.orgId, userIds, {
          emailFallback: false,
        });
        for (const id of userIds) set('user', id, names.get(id));
      }),
    teamIds.length > 0 &&
      deps.teamLookup &&
      attempt('teams', async () => {
        const infos = await deps.teamLookup?.describeMany(teamIds, identity);
        for (const id of teamIds) {
          const info = infos?.get(id);
          if (info?.status === 'ok') set('team', id, info.name);
        }
      }),
    agentIds.length > 0 &&
      deps.agents &&
      attempt('agents', async () => {
        for (const id of agentIds) {
          set('agent', id, (await deps.agents?.describe(identity, id))?.name);
        }
      }),
  ]);
  return labels;
}
