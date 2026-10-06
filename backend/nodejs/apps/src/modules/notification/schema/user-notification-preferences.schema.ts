import mongoose, { Schema, Model, Document, Types } from 'mongoose';

export const MUTED_SESSIONS_MAX = 500;
export const TIPS_SEEN_MAX = 50;
export const TIP_IDS = [
  'mentions.firstSharedSend',
  'mentions.firstNote',
  'mentions.firstAgentMention',
  'mentions.popoverIntro',
] as const;
export type TipId = (typeof TIP_IDS)[number];

export interface IUserNotificationPreferences {
  orgId: Types.ObjectId;
  userId: Types.ObjectId;
  email: {
    chatShared: boolean;
    ownershipTransferred: boolean;
    chatMentioned: boolean;
  };
  inApp: { chatActivity: boolean; chatMentioned: boolean };
  mutedSessions: Types.ObjectId[];
  tipsSeen: TipId[];
  schemaVersion?: number;
  createdAt?: Date;
  updatedAt?: Date;
}

export interface IUserNotificationPreferencesDocument
  extends Document,
    IUserNotificationPreferences {}

const userNotificationPreferencesSchema =
  new Schema<IUserNotificationPreferencesDocument>(
    {
      orgId: { type: Schema.Types.ObjectId, required: true },
      userId: { type: Schema.Types.ObjectId, required: true },
      email: {
        chatShared: { type: Boolean, default: true },
        ownershipTransferred: { type: Boolean, default: true },
        chatMentioned: { type: Boolean, default: false },
      },
      inApp: {
        chatActivity: { type: Boolean, default: true },
        chatMentioned: { type: Boolean, default: true },
      },
      mutedSessions: {
        type: [Schema.Types.ObjectId],
        default: [],
        validate: {
          validator: (ids: unknown[]) => ids.length <= MUTED_SESSIONS_MAX,
          message: `mutedSessions exceeds ${String(MUTED_SESSIONS_MAX)} entries`,
        },
      },
      tipsSeen: {
        type: [{ type: String, enum: TIP_IDS }],
        default: [],
        validate: {
          validator: (ids: unknown[]) => ids.length <= TIPS_SEEN_MAX,
          message: `tipsSeen exceeds ${String(TIPS_SEEN_MAX)} entries`,
        },
      },
      schemaVersion: { type: Number, default: 1 },
    },
    {
      timestamps: true,
      versionKey: false,
      collection: 'userNotificationPreferences',
    },
  );

userNotificationPreferencesSchema.index(
  { orgId: 1, userId: 1 },
  { unique: true },
);

export const UserNotificationPreferences: Model<IUserNotificationPreferencesDocument> =
  mongoose.model<IUserNotificationPreferencesDocument>(
    'UserNotificationPreferences',
    userNotificationPreferencesSchema,
  );
