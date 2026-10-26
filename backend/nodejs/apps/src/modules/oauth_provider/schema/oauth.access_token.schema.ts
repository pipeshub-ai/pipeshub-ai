import mongoose, { Document, Schema, Model, Types } from 'mongoose';
import { KnowledgeLimit } from '../types/oauth.types';
import { knowledgeLimitField } from './knowledge_limit.schema';

export interface IOAuthAccessToken extends Document {
  tokenHash: string;
  clientId: string;
  userId?: Types.ObjectId;
  orgId: Types.ObjectId;
  scopes: string[];
  expiresAt: Date;
  isRevoked: boolean;
  revokedAt?: Date;
  revokedBy?: Types.ObjectId;
  revokedReason?: string;
  parentRefreshTokenId?: Types.ObjectId;
  /** User-given label. Set for personal access tokens; undefined for
   * tokens minted through the interactive OAuth grant flows. */
  name?: string;
  /** Last time this token authenticated a request, updated best-effort
   * (throttled) so a PAT list can show recency without a write per call. */
  lastUsedAt?: Date;
  /**
   * The service account acting as `userId`, on a delegated token only.
   *
   * `userId` is the person the token speaks as, which is what every
   * authorisation check must read. This records who obtained it, so a
   * delegated token can be found and revoked when the service account behind
   * it is switched off — searching by `userId` would never find it, because
   * that column names the borrowed identity rather than the borrower.
   */
  actorUserId?: Types.ObjectId;
  /** What the token may see; absent for everything its user can. */
  knowledgeLimit?: KnowledgeLimit;
  createdAt: Date;
}

const OAuthAccessTokenSchema = new Schema<IOAuthAccessToken>(
  {
    tokenHash: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    clientId: {
      type: String,
      required: true,
      index: true,
    },
    userId: {
      type: Schema.Types.ObjectId,
      ref: 'users',
    },
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'org',
      required: true,
    },
    scopes: { type: [String], required: true },
    expiresAt: {
      type: Date,
      required: true,
      index: { expireAfterSeconds: 0 },
    },
    isRevoked: { type: Boolean, default: false },
    revokedAt: { type: Date },
    revokedBy: { type: Schema.Types.ObjectId, ref: 'users' },
    revokedReason: { type: String },
    parentRefreshTokenId: {
      type: Schema.Types.ObjectId,
      ref: 'oauthRefreshToken',
    },
    name: { type: String, trim: true, maxlength: 100 },
    lastUsedAt: { type: Date },
    actorUserId: { type: Schema.Types.ObjectId, ref: 'users' },
    knowledgeLimit: knowledgeLimitField,
  },
  { timestamps: true },
);

OAuthAccessTokenSchema.index({ clientId: 1, userId: 1, isRevoked: 1 });
OAuthAccessTokenSchema.index({ clientId: 1, isRevoked: 1 });
OAuthAccessTokenSchema.index({ actorUserId: 1, isRevoked: 1 });

export const OAuthAccessToken: Model<IOAuthAccessToken> =
  mongoose.model<IOAuthAccessToken>(
    'oauthAccessToken',
    OAuthAccessTokenSchema,
    'oauthAccessTokens',
  );
