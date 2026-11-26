import { Types } from 'mongoose';
import { ChatSession } from '../../../../modules/enterprise_search/schema/chat.session.schema';
import { Users } from '../../../../modules/user_management/schema/users.schema';
import {
  asMongoSession,
  type StoreSession,
} from '../durable-transaction/mongo';

/**
 * The reads and writes a person's deletion (CLEANUP-G2) and deactivation make on the platform's own user and
 * conversation collections.
 */

/** The org's earliest active admin other than `except`, by account age then id; null when there is none. */
export async function earliestActiveAdmin(
  orgId: string,
  except: string,
): Promise<string | null> {
  const admin = await Users.findOne({
    orgId: new Types.ObjectId(orgId),
    _id: { $ne: new Types.ObjectId(except) },
    role: 'admin',
    isDeleted: false,
    isDisabled: { $ne: true },
    kind: { $ne: 'service' },
  })
    .sort({ createdAt: 1, _id: 1 })
    .select('_id')
    .read('primary')
    .lean();
  return admin === null ? null : String(admin._id);
}

/** A member's account turned off (deactivation) or back on; the sessions it issued end by the activity recorded first. */
export async function setAccountDisabled(
  orgId: string,
  userId: string,
  disabled: boolean,
): Promise<void> {
  await Users.updateOne(
    { _id: new Types.ObjectId(userId), orgId: new Types.ObjectId(orgId) },
    { $set: { isDisabled: disabled } },
  );
}

const OWNED_BATCH = 10_000;

/** A user's own live conversations; `shared` when someone else holds a share of it. */
export async function ownedConversations(
  orgId: string,
  userId: string,
): Promise<Array<{ sessionId: string; shared: boolean }>> {
  const owned = await ChatSession.find(
    {
      orgId: new Types.ObjectId(orgId),
      userId: new Types.ObjectId(userId),
      isDeleted: { $ne: true },
    },
    { _id: 1, isShared: 1, sharedWith: 1 },
  )
    // The whole list is read, so one reply rather than the driver's default first batch of 101 and a getMore.
    .batchSize(OWNED_BATCH)
    .read('primary')
    .lean();
  return owned.map((session) => ({
    sessionId: String(session._id),
    shared: session.isShared === true && (session.sharedWith ?? []).length > 0,
  }));
}

/** Ids per write: one statement's filter stays well inside a command's size, whatever the person owns. */
const WRITE_CHUNK = 1_000;

const chunksOf = (ids: readonly string[]): Types.ObjectId[][] =>
  Array.from({ length: Math.ceil(ids.length / WRITE_CHUNK) }, (_, index) =>
    ids
      .slice(index * WRITE_CHUNK, (index + 1) * WRITE_CHUNK)
      .map((id) => new Types.ObjectId(id)),
  );

/** The user's conversations among `sessionIds` become `to`'s (owner and initiator), and `to` no longer holds a share of them, in `tx`. */
export async function transferConversations(
  orgId: string,
  fromUserId: string,
  sessionIds: readonly string[],
  toUserId: string,
  txHandle: StoreSession,
): Promise<void> {
  const tx = asMongoSession(txHandle);
  const to = new Types.ObjectId(toUserId);
  for (const ids of chunksOf(sessionIds)) {
    await ChatSession.updateMany(
      {
        _id: { $in: ids },
        orgId: new Types.ObjectId(orgId),
        userId: new Types.ObjectId(fromUserId),
      },
      {
        $set: { userId: to, initiator: to },
        $pull: { sharedWith: { userId: to } },
      },
      { session: tx },
    );
  }
}

/** The conversations are marked deleted, their erasure asked for at `now`, in `tx`. */
export async function markConversationsErased(
  orgId: string,
  sessionIds: readonly string[],
  now: number,
  txHandle: StoreSession,
): Promise<void> {
  const tx = asMongoSession(txHandle);
  for (const ids of chunksOf(sessionIds)) {
    await ChatSession.updateMany(
      { _id: { $in: ids }, orgId: new Types.ObjectId(orgId) },
      { $set: { isDeleted: true, erasureRequestedAt: now } },
      { session: tx },
    );
  }
}
