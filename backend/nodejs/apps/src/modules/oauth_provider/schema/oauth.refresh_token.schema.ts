import mongoose, { Document, Schema, Model, Types } from 'mongoose'

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
  rotationCount: number
  previousTokenHash?: string
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
    rotationCount: { type: Number, default: 0 },
    previousTokenHash: { type: String },
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
