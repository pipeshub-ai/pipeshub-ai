import mongoose, { Document, Schema, Model, Types } from 'mongoose'

export interface IAuthorizationCode extends Document {
  code: string
  codeChallenge?: string
  codeChallengeMethod?: 'S256' | 'plain'
  clientId: string
  userId: Types.ObjectId
  orgId: Types.ObjectId
  redirectUri: string
  scopes: string[]
  expiresAt: Date
  isUsed: boolean
  usedAt?: Date
  createdAt: Date
}

const AuthorizationCodeSchema = new Schema<IAuthorizationCode>(
  {
    code: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    codeChallenge: { type: String },
    codeChallengeMethod: {
      type: String,
      enum: ['S256', 'plain'],
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
    redirectUri: { type: String, required: true },
    scopes: { type: [String], required: true },
    expiresAt: {
      type: Date,
      required: true,
      index: { expireAfterSeconds: 0 },
    },
    isUsed: { type: Boolean, default: false },
    usedAt: { type: Date },
  },
  { timestamps: true },
)

AuthorizationCodeSchema.index({ code: 1, isUsed: 1 })
AuthorizationCodeSchema.index({ clientId: 1, userId: 1 })

export const AuthorizationCode: Model<IAuthorizationCode> =
  mongoose.model<IAuthorizationCode>(
    'authorizationCode',
    AuthorizationCodeSchema,
    'authorizationCodes',
  )
