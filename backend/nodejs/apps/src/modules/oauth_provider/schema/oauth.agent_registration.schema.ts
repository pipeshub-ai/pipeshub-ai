import mongoose, { Document, Schema, Model, Types } from 'mongoose';
import { AgentRegistrationStatus } from '../../../libs/repositories/oauth-token/types';

export { AgentRegistrationStatus };

export interface IOAuthAgentRegistration extends Document {
  claimEmail: string;
  claimTokenHash: string;
  attemptTokenHash: string;
  userCodeHash: string;
  attemptExpiresAt: Date;
  expiresAt: Date;
  purgeAt: Date;
  status: AgentRegistrationStatus;
  failedAttempts: number;
  lastPolledAt?: Date;
  claimedUserId?: Types.ObjectId;
  claimedOrgId?: Types.ObjectId;
  createdAt: Date;
  updatedAt: Date;
}

const OAuthAgentRegistrationSchema = new Schema<IOAuthAgentRegistration>(
  {
    claimEmail: { type: String, required: true },
    claimTokenHash: { type: String, required: true, unique: true, index: true },
    attemptTokenHash: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    userCodeHash: { type: String, required: true },
    attemptExpiresAt: { type: Date, required: true },
    expiresAt: { type: Date, required: true },
    // Deleted by the database once past; later than `expiresAt` so a consumed row keeps backing its assertion.
    purgeAt: { type: Date, required: true, index: { expireAfterSeconds: 0 } },
    status: {
      type: String,
      enum: Object.values(AgentRegistrationStatus),
      default: AgentRegistrationStatus.PENDING,
    },
    failedAttempts: { type: Number, default: 0 },
    lastPolledAt: { type: Date },
    claimedUserId: { type: Schema.Types.ObjectId, ref: 'users', index: true },
    claimedOrgId: { type: Schema.Types.ObjectId, ref: 'org' },
  },
  { timestamps: true },
);

export const OAuthAgentRegistration: Model<IOAuthAgentRegistration> =
  mongoose.model<IOAuthAgentRegistration>(
    'oauthAgentRegistration',
    OAuthAgentRegistrationSchema,
    'oauthAgentRegistrations',
  );
