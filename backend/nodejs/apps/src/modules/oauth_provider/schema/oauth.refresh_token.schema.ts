import mongoose, { Document, Schema, Model, Types } from 'mongoose'
import { KnowledgeLimit } from '../types/oauth.types';
import { knowledgeLimitField } from './knowledge_limit.schema';

export interface IOAuthRefreshToken extends Document {
  tokenHash: string
  clientId: string
  userId: Types.ObjectId
  orgId: Types.ObjectId
  scopes: string[]
  expiresAt: Date
  isRevoked: boolean
  revokedAt?: Date
  revokedBy?: Types.ObjectId
  revokedReason?: string
  rotationCount: number
  previousTokenHash?: string
  /** The limit the user agreed to, which each refresh carries forward.
   * `null` is no limit; absent marks a token issued before this was stored,
   * which a refresh gives the app's current limit. */
  knowledgeLimit?: KnowledgeLimit | null;
  createdAt: Date
}

const OAuthRefreshTokenSchema = new Schema<IOAuthRefreshToken>(
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
      required: true,
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
    rotationCount: { type: Number, default: 0 },
    previousTokenHash: { type: String },
    knowledgeLimit: knowledgeLimitField,
  },
  { timestamps: true },
)

OAuthRefreshTokenSchema.index({ clientId: 1, userId: 1, isRevoked: 1 })
OAuthRefreshTokenSchema.index({ clientId: 1, isRevoked: 1 })

export const OAuthRefreshToken: Model<IOAuthRefreshToken> =
  mongoose.model<IOAuthRefreshToken>(
    'oauthRefreshToken',
    OAuthRefreshTokenSchema,
    'oauthRefreshTokens',
  )
