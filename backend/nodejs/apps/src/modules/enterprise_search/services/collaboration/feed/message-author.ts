import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { FeedAuthor } from '../domain/collaboration-views';

/** The stored fields that say who a row belongs to. */
export interface AuthoredRow {
  messageType: string;
  authorUserId?: { toString(): string };
  requestedBy?: { toString(): string };
}

/**
 * Who a row is attributed to: the question's or note's author (legacy rows read as the owner, O-1), an answer's
 * `requestedBy`. The feed and the conversation detail both go through this.
 */
export function authorIdOf(
  row: AuthoredRow,
  ownerId: string,
): string | undefined {
  return row.messageType === 'user_query' || row.messageType === 'note'
    ? (row.authorUserId?.toString() ?? ownerId)
    : row.requestedBy?.toString();
}

/** Ids whose names a set of rows needs, the owner included. */
export function authorIdsNeeded(
  rows: readonly AuthoredRow[],
  ownerId: string,
): Set<string> {
  const ids = new Set<string>([ownerId]);
  for (const row of rows) {
    const id = row.authorUserId?.toString() ?? row.requestedBy?.toString();
    if (id !== undefined) {
      ids.add(id);
    }
  }
  return ids;
}

export function authorViewOf(
  row: AuthoredRow,
  ownerId: string,
  names: ReadonlyMap<string, string>,
): FeedAuthor | undefined {
  const id = authorIdOf(row, ownerId);
  return id === undefined
    ? undefined
    : { userId: id, displayName: names.get(id) ?? '' };
}

export interface DetailCollabFields {
  seq: number;
  author?: FeedAuthor;
}

type DetailRow = AuthoredRow & { _id: unknown; seq: number };

/**
 * `seq` and `author` for each row of the conversation detail, keyed by row id, so a tab that has only
 * loaded the detail can name the other people and send `baseSeq`. Same mapping as the feed.
 */
export async function detailCollabFields(
  users: IUserDirectory,
  orgId: string,
  ownerId: string,
  rows: readonly DetailRow[],
): Promise<Map<string, DetailCollabFields>> {
  const names = await users.displayNames(orgId, [
    ...authorIdsNeeded(rows, ownerId),
  ]);
  return new Map(
    rows.map((row) => {
      const author = authorViewOf(row, ownerId, names);
      return [
        String(row._id),
        { seq: row.seq, ...(author !== undefined && { author }) },
      ];
    }),
  );
}

/** Adds the fields to the matching rows of a detail response; rows without an entry are left alone. */
export function withDetailCollabFields<T extends object>(
  rows: readonly T[],
  fields: ReadonlyMap<string, DetailCollabFields>,
): Array<T & Partial<DetailCollabFields>> {
  return rows.map((row) => ({
    ...row,
    ...fields.get(String((row as { _id?: unknown })._id)),
  }));
}

/** The detail response with `seq` and `author` on each row when collaborative chats is on; unchanged otherwise. */
export async function withCollabMessageFields<R extends { messages: object[] }>(
  response: R,
  rows: readonly DetailRow[],
  users: IUserDirectory,
  orgId: string | undefined,
  ownerId: string,
  enabled: boolean,
): Promise<R> {
  if (!enabled || !orgId) {
    return response;
  }
  const fields = await detailCollabFields(users, orgId, ownerId, rows);
  return {
    ...response,
    messages: withDetailCollabFields(response.messages, fields),
  };
}
