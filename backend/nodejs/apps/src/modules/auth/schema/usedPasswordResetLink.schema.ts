import { createHash } from 'crypto';
import mongoose, { Schema, Model } from 'mongoose';

/**
 * A password-reset link that has been used. Inserting the row is how a request
 * claims the link: the unique index lets exactly one of two simultaneous
 * requests in, where comparing against the PASSWORD_CHANGED activity cannot,
 * because that is written only once the reset has finished.
 */
export interface IUsedPasswordResetLink {
  linkHash: string;
  userId: string;
  orgId: string;
  // Rows are kept until the link would have expired anyway, then removed.
  expiresAt: Date;
}

const usedPasswordResetLinkSchema = new Schema<IUsedPasswordResetLink>(
  {
    linkHash: { type: String, required: true, unique: true },
    userId: { type: String, required: true },
    orgId: { type: String, required: true },
    expiresAt: {
      type: Date,
      required: true,
      index: { expireAfterSeconds: 0 },
    },
  },
  { timestamps: true },
);

export const UsedPasswordResetLink: Model<IUsedPasswordResetLink> =
  mongoose.model<IUsedPasswordResetLink>(
    'usedPasswordResetLink',
    usedPasswordResetLinkSchema,
    'usedPasswordResetLinks',
  );

/** Stored instead of the link itself, which would still work until it expires. */
export function hashResetLink(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}
