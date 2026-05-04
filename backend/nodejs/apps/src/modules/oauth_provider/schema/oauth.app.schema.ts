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

/**
 * How the OAuthApp was registered. Used to gate cleanup policies and
 * to render badges on the consent screen.
 */
export enum OAuthAppRegisteredVia {
  /** Created by an org admin via /api/v1/oauth-clients. */
  ADMIN = 'admin',
  /** Self-registered via the public /api/v1/oauth2/register (RFC 7591). */
  DCR = 'dcr',
}

export type OAuthTokenEndpointAuthMethod =
  | 'none'
  | 'client_secret_basic'
  | 'client_secret_post'

export type OAuthApplicationType = 'native' | 'web'

export interface IOAuthApp extends Document {
  slug: string
  clientId: string
  /** Empty string for public DCR clients (token_endpoint_auth_method = 'none'). */
  clientSecretEncrypted: string
  name: string
  description?: string
  /**
   * Required for ADMIN-registered apps; absent for DCR-registered apps until the
   * first user completes /authorize (then the /authorize controller adopts it).
   */
  orgId?: Types.ObjectId
  createdBy?: Types.ObjectId
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
  /** User who performed the soft-delete (audit). */
  deletedBy?: Types.ObjectId
  // ----- DCR-specific fields (RFC 7591 / RFC 7592) -----
  registeredVia: OAuthAppRegisteredVia
  /** Free-form policy tag, e.g. "mcp.dcr.public". */
  clientProfile?: string
  /** sha256 hex of registration_access_token; only set when registeredVia = DCR. */
  registrationAccessTokenHash?: string
  tokenEndpointAuthMethod?: OAuthTokenEndpointAuthMethod
  responseTypes?: string[]
  contacts?: string[]
  softwareId?: string
  softwareVersion?: string
  applicationType?: OAuthApplicationType
  /** Bumped on every successful /authorize. Used by the cleanup job. */
  lastAuthorizedAt?: Date
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
    /**
     * Empty string for public DCR clients (token_endpoint_auth_method = 'none').
     * Optional so Mongoose doesn't fail validation on the empty-string default
     * (Mongoose treats '' as missing under `required: true`).
     */
    clientSecretEncrypted: { type: String, default: '' },
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
    /**
     * Required for ADMIN-registered apps; absent for DCR until the first user
     * completes /authorize against the client. The /authorize controller
     * adopts the client (orgId + createdBy) on first successful consent.
     */
    orgId: {
      type: Schema.Types.ObjectId,
      ref: 'org',
      required: false,
      index: true,
    },
    createdBy: {
      type: Schema.Types.ObjectId,
      ref: 'users',
      required: false,
    },
    redirectUris: {
      type: [String],
      default: [],
      validate: {
        validator: function (uris: string[]) {
          return uris.length <= 10
        },
        message: 'Must have at most 10 redirect URIs',
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
    // ----- DCR (RFC 7591) -----
    registeredVia: {
      type: String,
      enum: Object.values(OAuthAppRegisteredVia),
      default: OAuthAppRegisteredVia.ADMIN,
      required: true,
      index: true,
    },
    clientProfile: { type: String },
    registrationAccessTokenHash: { type: String },
    tokenEndpointAuthMethod: {
      type: String,
      enum: ['none', 'client_secret_basic', 'client_secret_post'],
    },
    responseTypes: { type: [String], default: undefined },
    contacts: { type: [String], default: undefined },
    softwareId: { type: String },
    softwareVersion: { type: String },
    applicationType: { type: String, enum: ['native', 'web'] },
    lastAuthorizedAt: { type: Date },
  },
  { timestamps: true },
)

OAuthAppSchema.index({ orgId: 1, isDeleted: 1 })
/** Supports creator-scoped listing (`buildAppFilter`) with `sort({ createdAt: -1 })` — deploy with syncIndexes/migrate as needed. */
OAuthAppSchema.index({ orgId: 1, createdBy: 1, isDeleted: 1, createdAt: -1 })
OAuthAppSchema.index({ clientId: 1, status: 1 })
/** Supports DCR cleanup job: `find({ registeredVia: 'dcr', lastAuthorizedAt: { $lt: cutoff } })`. */
OAuthAppSchema.index({ registeredVia: 1, lastAuthorizedAt: 1 })

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
