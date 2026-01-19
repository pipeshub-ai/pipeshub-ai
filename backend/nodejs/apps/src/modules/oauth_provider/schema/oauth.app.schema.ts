import mongoose, { Document, Schema, Model, Types } from 'mongoose'
import { generateUniqueSlug } from '../../../libs/utils/counter'

export enum OAuthAppStatus {
  ACTIVE = 'active',
  SUSPENDED = 'suspended',
  REVOKED = 'revoked',
}

export enum OAuthGrantType {
  AUTHORIZATION_CODE = 'authorization_code',
  CLIENT_CREDENTIALS = 'client_credentials',
  REFRESH_TOKEN = 'refresh_token',
}

export interface IOAuthApp extends Document {
  slug: string
  clientId: string
  clientSecretEncrypted: string
  name: string
  description?: string
  orgId: Types.ObjectId
  createdBy: Types.ObjectId
  redirectUris: string[]
  allowedGrantTypes: OAuthGrantType[]
  allowedScopes: string[]
  status: OAuthAppStatus
  logoUrl?: string
  homepageUrl?: string
  privacyPolicyUrl?: string
  termsOfServiceUrl?: string
  isConfidential: boolean
  accessTokenLifetime: number
  refreshTokenLifetime: number
  isDeleted: boolean
  deletedBy?: Types.ObjectId
  createdAt: Date
  updatedAt: Date
}

const OAuthAppSchema = new Schema<IOAuthApp>(
  {
    slug: { type: String, unique: true },
    clientId: {
      type: String,
      required: true,
      unique: true,
      index: true,
    },
    clientSecretEncrypted: { type: String, required: true },
    name: {
      type: String,
      required: [true, 'App name is required'],
      trim: true,
      maxlength: 100,
    },
    description: {
      type: String,
      trim: true,
      maxlength: 500,
    },
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'org',
      required: true,
      index: true,
    },
    createdBy: {
      type: Schema.Types.ObjectId,
      ref: 'users',
      required: true,
    },
    redirectUris: {
      type: [String],
      required: true,
      validate: {
        validator: function (uris: string[]) {
          return uris.length > 0 && uris.length <= 10
        },
        message: 'Must have between 1 and 10 redirect URIs',
      },
    },
    allowedGrantTypes: {
      type: [String],
      enum: Object.values(OAuthGrantType),
      default: [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN],
    },
    allowedScopes: {
      type: [String],
      required: true,
      validate: {
        validator: function (scopes: string[]) {
          return scopes.length > 0
        },
        message: 'At least one scope is required',
      },
    },
    status: {
      type: String,
      enum: Object.values(OAuthAppStatus),
      default: OAuthAppStatus.ACTIVE,
    },
    logoUrl: { type: String },
    homepageUrl: { type: String },
    privacyPolicyUrl: { type: String },
    termsOfServiceUrl: { type: String },
    isConfidential: { type: Boolean, default: true },
    accessTokenLifetime: { type: Number, default: 3600 },
    refreshTokenLifetime: { type: Number, default: 2592000 },
    isDeleted: { type: Boolean, default: false },
    deletedBy: { type: Schema.Types.ObjectId, ref: 'users' },
  },
  { timestamps: true },
)

OAuthAppSchema.index({ orgId: 1, isDeleted: 1 })
OAuthAppSchema.index({ clientId: 1, status: 1 })

OAuthAppSchema.pre<IOAuthApp>('save', async function (next) {
  try {
    if (!this.slug) {
      this.slug = await generateUniqueSlug('OAuthApp')
    }
    next()
  } catch (error) {
    next(error as Error)
  }
})

export const OAuthApp: Model<IOAuthApp> = mongoose.model<IOAuthApp>(
  'oauthApp',
  OAuthAppSchema,
  'oauthApps',
)
