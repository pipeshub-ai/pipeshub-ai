import { Types } from 'mongoose';
import {
  MUTED_SESSIONS_MAX,
  TIPS_SEEN_MAX,
  TipId,
  UserNotificationPreferences,
} from '../schema/user-notification-preferences.schema';

export interface NotificationPreferences {
  email: {
    chatShared: boolean;
    ownershipTransferred: boolean;
    chatMentioned: boolean;
  };
  inApp: { chatActivity: boolean; chatMentioned: boolean };
  mutedSessions: string[];
  tipsSeen: string[];
}

export interface NotificationPreferencesPatch {
  email?: Partial<NotificationPreferences['email']>;
  inApp?: Partial<NotificationPreferences['inApp']>;
}

export type MuteOutcome = 'muted' | 'already_muted' | 'limit';

export const defaultNotificationPreferences = (): NotificationPreferences => ({
  email: { chatShared: true, ownershipTransferred: true, chatMentioned: false },
  inApp: { chatActivity: true, chatMentioned: true },
  mutedSessions: [],
  tipsSeen: [],
});

/** `.lean()` skips schema defaults, so any field may be missing on a stored doc. */
interface StoredPreferences {
  email?: Partial<NotificationPreferences['email']>;
  inApp?: Partial<NotificationPreferences['inApp']>;
  mutedSessions?: Types.ObjectId[];
  tipsSeen?: string[];
}

export interface INotificationPreferencesRepository {
  get(orgId: string, userId: string): Promise<NotificationPreferences>;
  /** One query; every requested user is present, with defaults when they have no row. */
  getMany(
    orgId: string,
    userIds: readonly string[],
  ): Promise<ReadonlyMap<string, NotificationPreferences>>;
  update(
    orgId: string,
    userId: string,
    patch: NotificationPreferencesPatch,
  ): Promise<NotificationPreferences>;
  muteSession(
    orgId: string,
    userId: string,
    sessionId: string,
  ): Promise<MuteOutcome>;
  unmuteSession(
    orgId: string,
    userId: string,
    sessionId: string,
  ): Promise<void>;
  /** Idempotent (`$addToSet`); a full list ignores a new tip rather than failing. */
  markTipSeen(
    orgId: string,
    userId: string,
    tipId: TipId,
  ): Promise<NotificationPreferences>;
}

const fromStored = (doc: StoredPreferences): NotificationPreferences => ({
  email: {
    chatShared: doc.email?.chatShared ?? true,
    ownershipTransferred: doc.email?.ownershipTransferred ?? true,
    chatMentioned: doc.email?.chatMentioned ?? false,
  },
  inApp: {
    chatActivity: doc.inApp?.chatActivity ?? true,
    chatMentioned: doc.inApp?.chatMentioned ?? true,
  },
  mutedSessions: (doc.mutedSessions ?? []).map(String),
  tipsSeen: (doc.tipsSeen ?? []).map(String),
});

const keyOf = (
  orgId: string,
  userId: string,
): { orgId: Types.ObjectId; userId: Types.ObjectId } => ({
  orgId: new Types.ObjectId(orgId),
  userId: new Types.ObjectId(userId),
});

export class MongoNotificationPreferencesRepository
  implements INotificationPreferencesRepository
{
  async get(orgId: string, userId: string): Promise<NotificationPreferences> {
    const doc = (await UserNotificationPreferences.findOne(keyOf(orgId, userId))
      .lean()
      .exec()) as StoredPreferences | null;
    if (!doc) {
      return defaultNotificationPreferences();
    }
    return fromStored(doc);
  }

  async getMany(
    orgId: string,
    userIds: readonly string[],
  ): Promise<ReadonlyMap<string, NotificationPreferences>> {
    const result = new Map(
      userIds.map((id) => [id, defaultNotificationPreferences()] as const),
    );
    if (userIds.length === 0) {
      return result;
    }
    const docs = (await UserNotificationPreferences.find({
      orgId: new Types.ObjectId(orgId),
      userId: { $in: userIds.map((id) => new Types.ObjectId(id)) },
    })
      .lean()
      .exec()) as Array<StoredPreferences & { userId: Types.ObjectId }>;
    for (const doc of docs) {
      result.set(doc.userId.toString(), fromStored(doc));
    }
    return result;
  }

  async update(
    orgId: string,
    userId: string,
    patch: NotificationPreferencesPatch,
  ): Promise<NotificationPreferences> {
    const $set: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(patch.email ?? {})) {
      if (typeof value === 'boolean') {
        $set[`email.${key}`] = value;
      }
    }
    for (const [key, value] of Object.entries(patch.inApp ?? {})) {
      if (typeof value === 'boolean') {
        $set[`inApp.${key}`] = value;
      }
    }
    if (Object.keys($set).length > 0) {
      await UserNotificationPreferences.updateOne(
        keyOf(orgId, userId),
        { $set },
        { upsert: true },
      );
    }
    return this.get(orgId, userId);
  }

  async muteSession(
    orgId: string,
    userId: string,
    sessionId: string,
  ): Promise<MuteOutcome> {
    const key = keyOf(orgId, userId);
    const sid = new Types.ObjectId(sessionId);
    await UserNotificationPreferences.updateOne(
      key,
      { $setOnInsert: key },
      { upsert: true },
    );
    const res = await UserNotificationPreferences.updateOne(
      {
        ...key,
        mutedSessions: { $ne: sid },
        [`mutedSessions.${String(MUTED_SESSIONS_MAX - 1)}`]: { $exists: false },
      },
      { $addToSet: { mutedSessions: sid } },
    );
    if (res.modifiedCount === 1) {
      return 'muted';
    }
    const current = (await UserNotificationPreferences.findOne(key, {
      mutedSessions: 1,
    })
      .lean()
      .exec()) as StoredPreferences | null;
    return current?.mutedSessions?.some((id) => id.equals(sid))
      ? 'already_muted'
      : 'limit';
  }

  async unmuteSession(
    orgId: string,
    userId: string,
    sessionId: string,
  ): Promise<void> {
    await UserNotificationPreferences.updateOne(keyOf(orgId, userId), {
      $pull: { mutedSessions: new Types.ObjectId(sessionId) },
    });
  }

  async markTipSeen(
    orgId: string,
    userId: string,
    tipId: TipId,
  ): Promise<NotificationPreferences> {
    const key = keyOf(orgId, userId);
    await UserNotificationPreferences.updateOne(
      key,
      { $setOnInsert: key },
      { upsert: true },
    );
    await UserNotificationPreferences.updateOne(
      {
        ...key,
        [`tipsSeen.${String(TIPS_SEEN_MAX - 1)}`]: { $exists: false },
      },
      { $addToSet: { tipsSeen: tipId } },
    );
    return this.get(orgId, userId);
  }
}
