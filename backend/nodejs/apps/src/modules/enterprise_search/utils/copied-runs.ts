import { runIdsOfSession as mongoRunIdsOfSession } from '../../../libs/repositories/agent-harness/execution/mongo';
import { pgRunIdsOfSession } from '../../../libs/repositories/agent-harness/execution/pg';
import { pickHarnessStore } from '../../../libs/repositories/agent-harness/stores';
import type { IMessage, IMessagePart } from '../types/conversation.interfaces';

/** Part fields that name a durable run; the routes that read a run take it only under its own conversation. */
const RUN_REFERENCES: ReadonlySet<string> = new Set(['runId', 'parentRunId']);

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

function partsWithoutRuns(
  parts: IMessagePart[] | undefined,
): IMessagePart[] | undefined {
  if (!Array.isArray(parts)) return parts;
  return parts.map((part) => {
    // Persisted parts are Mixed: an element is not guaranteed to be an object.
    const candidate: unknown = part;
    if (!isObject(candidate)) return part;
    const copy: Record<string, unknown> = Object.fromEntries(
      Object.entries(candidate).filter(([key]) => !RUN_REFERENCES.has(key)),
    );
    if (Array.isArray(copy['parts'])) {
      copy['parts'] = partsWithoutRuns(copy['parts'] as IMessagePart[]);
    }
    return copy as unknown as IMessagePart;
  });
}

const runIdOf = (message: Pick<IMessage, 'runId'>): string | null =>
  typeof message.runId === 'string' && message.runId !== ''
    ? message.runId
    : null;

/**
 * A message copied into another conversation: without the run ids it held,
 * since a run is read only through the conversation it ran in, and marked
 * `copied` so the web app shows its saved steps without offering the run's
 * own views. Never mutates its input.
 */
export function detachCopiedRuns<T extends Partial<IMessage>>(message: T): T {
  const { runId: _dropped, ...rest } = message;
  const detached: Partial<IMessage> = {
    ...rest,
    ...(Array.isArray(message.parts)
      ? { parts: partsWithoutRuns(message.parts) }
      : {}),
    copied: true,
  };
  return detached as T;
}

/** The forked-from conversation a conversation's metadata names, if any (a Map on documents, an object on lean reads). */
export function forkedFromOf(metadata: unknown): string | null {
  const value =
    metadata instanceof Map
      ? (metadata.get('forkedFromConversationId') as unknown)
      : isObject(metadata)
        ? metadata['forkedFromConversationId']
        : undefined;
  return typeof value === 'string' && value !== '' ? value : null;
}

/**
 * A fork's messages as the conversation reads serve them: an answer whose
 * run id names a run of another conversation (copied by a fork written before forks
 * detached them) is detached the way a fork now writes it. Reads only the
 * fork's own run ids, by its `{orgId, sessionId}` index; any other
 * conversation is returned as it is.
 */
export async function withForeignRunsDetached<T extends Partial<IMessage>>(
  conversation: { _id: unknown; metadata?: unknown },
  orgId: string,
  messages: T[],
): Promise<T[]> {
  if (forkedFromOf(conversation.metadata) === null) return messages;
  if (!messages.some((message) => runIdOf(message) !== null)) return messages;
  const runIdsOfSession = pickHarnessStore<
    (orgId: string, sessionId: string) => Promise<string[]>
  >('agent-harness/execution', {
    mongo: () => mongoRunIdsOfSession,
    postgres: () => pgRunIdsOfSession,
  });
  const own = new Set(await runIdsOfSession(orgId, String(conversation._id)));
  return messages.map((message) => {
    const runId = runIdOf(message);
    return runId !== null && !own.has(runId)
      ? detachCopiedRuns(message)
      : message;
  });
}
