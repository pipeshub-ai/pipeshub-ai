import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { IAgentProfiles } from './agent.directory';

/** Who answered a guest-agent turn, as the API shows it. */
export interface RespondingAgentView {
  readonly key: string;
  readonly name?: string;
  readonly handle?: string;
}

interface AnsweredRow {
  messageType?: string;
  respondingAgentKey?: string;
}

/** Name and handle at read time, as the caller sees them; an agent the caller cannot read is just its key. */
export async function respondingAgentViews(
  rows: readonly AnsweredRow[],
  identity: CallerIdentity | undefined,
  profiles: IAgentProfiles | undefined,
): Promise<ReadonlyMap<string, RespondingAgentView>> {
  const keys = new Set<string>();
  for (const row of rows) {
    if (row.messageType === 'bot_response' && row.respondingAgentKey) {
      keys.add(row.respondingAgentKey);
    }
  }
  const views = await Promise.all(
    [...keys].map(async (key): Promise<[string, RespondingAgentView]> => {
      const profile =
        identity && profiles ? await profiles.describe(identity, key) : undefined;
      return [
        key,
        profile
          ? {
              key,
              name: profile.name,
              ...(profile.handle !== undefined && { handle: profile.handle }),
            }
          : { key },
      ];
    }),
  );
  return new Map(views);
}

export function respondingAgentOf(
  row: AnsweredRow,
  views: ReadonlyMap<string, RespondingAgentView>,
): { respondingAgent: RespondingAgentView } | undefined {
  const view =
    row.messageType === 'bot_response' && row.respondingAgentKey
      ? views.get(row.respondingAgentKey)
      : undefined;
  return view ? { respondingAgent: view } : undefined;
}

/** The detail response with `respondingAgent` on each guest-agent answer; unchanged when there is none. */
export async function withRespondingAgents<R extends { messages: object[] }>(
  response: R,
  identity: CallerIdentity,
  profiles: IAgentProfiles | undefined,
): Promise<R> {
  const rows = response.messages as AnsweredRow[];
  const views = await respondingAgentViews(rows, identity, profiles);
  if (views.size === 0) return response;
  return {
    ...response,
    messages: rows.map((row) => ({ ...row, ...respondingAgentOf(row, views) })),
  };
}
