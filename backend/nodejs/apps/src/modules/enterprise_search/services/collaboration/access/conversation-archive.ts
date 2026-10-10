import { Types, UpdateQuery } from 'mongoose';
import { IChatSession } from '../../../types/conversation.interfaces';

type ArchiveFacts = Pick<
  IChatSession,
  'userId' | 'sharedWith' | 'projectVisibility' | 'isArchived' | 'archivedFor'
>;

export interface ArchiveState {
  /** True when archive is `archivedFor` membership rather than the global `isArchived`. */
  perUser: boolean;
  archived: boolean;
}

/** Per-user archive applies to shared chats, the owner included; an unshared owner chat keeps the global flag (D6v2). */
export function archiveStateOf(
  session: ArchiveFacts,
  userId: string,
  collab: boolean,
): ArchiveState {
  const shared =
    (session.sharedWith?.length ?? 0) > 0 ||
    session.projectVisibility === 'project';
  if (!collab || !shared) {
    return { perUser: false, archived: session.isArchived === true };
  }
  const isOwner = session.userId.toString() === userId;
  const archivedForCaller = (session.archivedFor ?? []).some(
    (id) => id.toString() === userId,
  );
  return {
    perUser: true,
    archived: archivedForCaller || (isOwner && session.isArchived === true),
  };
}

/** No `rev` bump and no `lastActivityAt`: per-user state is not a visible change to anyone else. */
export function perUserArchiveUpdate(
  action: 'archive' | 'unarchive',
  session: ArchiveFacts,
  userId: string,
): UpdateQuery<IChatSession> {
  const me = new Types.ObjectId(userId);
  if (action === 'archive') {
    return { $addToSet: { archivedFor: me } };
  }
  // An owner archive made before the first share is still global; unarchive clears it too.
  const clearsGlobal =
    session.isArchived === true && session.userId.toString() === userId;
  return {
    $pull: { archivedFor: me },
    ...(clearsGlobal && { $set: { isArchived: false, archivedBy: null } }),
  };
}
