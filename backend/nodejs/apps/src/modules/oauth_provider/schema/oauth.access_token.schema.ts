import mongoose, { Document, Schema, Model, Types } from 'mongoose'

export interface IOAuthAccessToken extends Document {
  tokenHash: string
  clientId: string
  userId?: Types.ObjectId
  orgId: Types.ObjectId
  scopes: string[]
  expiresAt: Date
  isRevoked: boolean
  revokedAt?: Date
  revokedBy?: Types.ObjectId
  revokedReason?: string
  parentRefreshTokenId?: Types.ObjectId
  createdAt: Date
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
  },
  { timestamps: true },
)

OAuthAccessTokenSchema.index({ clientId: 1, userId: 1, isRevoked: 1 })
OAuthAccessTokenSchema.index({ clientId: 1, isRevoked: 1 })

export const OAuthAccessToken: Model<IOAuthAccessToken> =
  mongoose.model<IOAuthAccessToken>(
    'oauthAccessToken',
    OAuthAccessTokenSchema,
    'oauthAccessTokens',
  )
