import {
  IReadStateRepository,
  MongoReadStateRepository,
} from '../persistence/read-state.repository';
import { ConversationRequestContext } from './conversation-context';

interface SharedListRow {
  _id: { toString(): string };
  userId?: { toString(): string };
  sharedWith?: readonly unknown[];
  projectVisibility?: string;
  nextSeq?: number;
}

const defaultReadStates: IReadStateRepository = new MongoReadStateRepository();

/** List queries add this to their `select` so the unread count can be computed. */
export const listSelect = (ctx: ConversationRequestContext): string =>
  ctx.collab === true ? '-__v +nextSeq' : '-__v';

const isCollaborative = (row: SharedListRow): boolean =>
  (row.sharedWith?.length ?? 0) > 0 || row.projectVisibility === 'project';

/**
 * Unread badge and collaborator count for the rows of one list page (PH-09). Flag off: rows pass through.
 * `unreadCount` is the messages after the caller's read position, for collaborative rows only, from one
 * read-state query for the whole page. `collaboratorCount` goes to the owner only; it is computed from the
 * raw rows because a recipient's row has already lost `sharedWith`. The internal `nextSeq` is always dropped.
 */
export async function sharedListDecorator(
  ctx: ConversationRequestContext,
  rawRows: readonly SharedListRow[],
  readStates: IReadStateRepository = defaultReadStates,
): Promise<<T extends object>(processed: T) => T> {
  if (ctx.collab !== true) {
    return (processed) => processed;
  }
  const collaborative = rawRows.filter(isCollaborative);
  const lastRead = await readStates.lastReadSeqs(
    ctx.caller.userId,
    collaborative.map((r) => r._id.toString()),
  );
  const extras = new Map<string, Record<string, number>>();
  for (const row of collaborative) {
    const id = row._id.toString();
    const read = Math.max(lastRead.get(id) ?? 0, 0);
    extras.set(id, {
      unreadCount: Math.max((row.nextSeq ?? 0) - read, 0),
      ...(row.userId?.toString() === ctx.caller.userId && {
        collaboratorCount: row.sharedWith?.length ?? 0,
      }),
    });
  }
  return <T extends object>(processed: T): T => {
    const { nextSeq: _nextSeq, ...rest } = processed as T & {
      nextSeq?: number;
      _id?: { toString(): string };
    };
    const id = (processed as { _id?: { toString(): string } })._id?.toString();
    return {
      ...rest,
      ...(id === undefined ? undefined : extras.get(id)),
    } as unknown as T;
  };
}
