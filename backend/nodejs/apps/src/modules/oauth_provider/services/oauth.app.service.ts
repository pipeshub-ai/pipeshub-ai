import { injectable, inject } from 'inversify'
import { v4 as uuidv4 } from 'uuid'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { ScopeValidatorService } from './scope.validator.service'
import {
  OAuthApp,
  IOAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../schema/oauth.app.schema'
import {
  InvalidClientError,
  InvalidRedirectUriError,
} from '../../../libs/errors/oauth.errors'
import { NotFoundError, BadRequestError } from '../../../libs/errors/http.errors'
import {
  CreateOAuthAppRequest,
  UpdateOAuthAppRequest,
  OAuthAppResponse,
  OAuthAppWithSecret,
  ListAppsQuery,
  PaginatedResponse,
} from '../types/oauth.types'

const CLIENT_SECRET_LENGTH = 32

@injectable()
export class OAuthAppService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EncryptionService') private encryptionService: EncryptionService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * Create a new OAuth app
   */
  async createApp(
    orgId: string,
    createdBy: string,
    data: CreateOAuthAppRequest,
  ): Promise<OAuthAppWithSecret> {
    // Validate scopes
    this.scopeValidatorService.validateRequestedScopes(data.allowedScopes)

    // Validate redirect URIs
    this.validateRedirectUris(data.redirectUris)

    // Validate grant types
    const allowedGrantTypes = data.allowedGrantTypes || [
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ]
    this.validateGrantTypes(allowedGrantTypes)

    // Generate credentials
    const clientId = uuidv4()
    const clientSecret = this.generateClientSecret()
    const clientSecretEncrypted = this.encryptionService.encrypt(clientSecret)

    const app = await OAuthApp.create({
      clientId,
      clientSecretEncrypted,
      name: data.name,
      description: data.description,
      orgId: new Types.ObjectId(orgId),
      createdBy: new Types.ObjectId(createdBy),
      redirectUris: data.redirectUris,
      allowedGrantTypes,
      allowedScopes: data.allowedScopes,
      homepageUrl: data.homepageUrl,
      privacyPolicyUrl: data.privacyPolicyUrl,
      termsOfServiceUrl: data.termsOfServiceUrl,
      isConfidential: data.isConfidential ?? true,
      accessTokenLifetime: data.accessTokenLifetime ?? 3600,
      refreshTokenLifetime: data.refreshTokenLifetime ?? 2592000,
    })

    this.logger.info('OAuth app created', {
      appId: (app._id as Types.ObjectId).toString(),
      clientId,
      orgId,
      name: data.name,
    })

    return {
      ...this.toAppResponse(app),
      clientSecret,
    }
  }

  /**
   * Get OAuth app by ID
   */
  async getAppById(appId: string, orgId: string): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    return this.toAppResponse(app)
  }

  /**
   * Get OAuth app by client ID
   */
  async getAppByClientId(clientId: string): Promise<IOAuthApp> {
    const app = await OAuthApp.findOne({
      clientId: { $eq: clientId },
      isDeleted: false,
    })

    if (!app) {
      throw new InvalidClientError('Invalid client_id')
    }

    if (app.status !== OAuthAppStatus.ACTIVE) {
      throw new InvalidClientError(
        `OAuth app is ${app.status}`,
        { status: app.status },
      )
    }

    return app
  }

  /**
   * List OAuth apps for organization
   */
  async listApps(
    orgId: string,
    query: ListAppsQuery,
  ): Promise<PaginatedResponse<OAuthAppResponse>> {
    const page = query.page || 1
    const limit = query.limit || 20
    const skip = (page - 1) * limit

    const filter: Record<string, unknown> = {
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    }

    if (query.status) {
      filter.status = { $eq: query.status }
    }

    if (query.search) {
      // Escape special regex characters to prevent ReDoS attacks
      const escapedSearch = query.search.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      filter.$or = [
        { name: { $regex: escapedSearch, $options: 'i' } },
        { description: { $regex: escapedSearch, $options: 'i' } },
      ]
    }

    const [apps, total] = await Promise.all([
      OAuthApp.find(filter)
        .sort({ createdAt: -1 })
        .skip(skip)
        .limit(limit)
        .exec(),
      OAuthApp.countDocuments(filter),
    ])

    return {
      data: apps.map((app) => this.toAppResponse(app)),
      pagination: {
        page,
        limit,
        total,
        totalPages: Math.ceil(total / limit),
      },
    }
  }

  /**
   * Update OAuth app
   */
  async updateApp(
    appId: string,
    orgId: string,
    data: UpdateOAuthAppRequest,
  ): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    // Validate scopes if provided
    if (data.allowedScopes) {
      this.scopeValidatorService.validateRequestedScopes(data.allowedScopes)
    }

    // Validate redirect URIs if provided
    if (data.redirectUris) {
      this.validateRedirectUris(data.redirectUris)
    }

    // Validate grant types if provided
    if (data.allowedGrantTypes) {
      this.validateGrantTypes(data.allowedGrantTypes)
    }

    // Update fields
    if (data.name !== undefined) app.name = data.name
    if (data.description !== undefined) app.description = data.description
    if (data.redirectUris !== undefined) app.redirectUris = data.redirectUris
    if (data.allowedGrantTypes !== undefined) {
      app.allowedGrantTypes = data.allowedGrantTypes as OAuthGrantType[]
    }
    if (data.allowedScopes !== undefined) app.allowedScopes = data.allowedScopes
    if (data.homepageUrl !== undefined) {
      app.homepageUrl = data.homepageUrl ?? undefined
    }
    if (data.privacyPolicyUrl !== undefined) {
      app.privacyPolicyUrl = data.privacyPolicyUrl ?? undefined
    }
    if (data.termsOfServiceUrl !== undefined) {
      app.termsOfServiceUrl = data.termsOfServiceUrl ?? undefined
    }
    if (data.accessTokenLifetime !== undefined) {
      app.accessTokenLifetime = data.accessTokenLifetime
    }
    if (data.refreshTokenLifetime !== undefined) {
      app.refreshTokenLifetime = data.refreshTokenLifetime
    }

    await app.save()

    this.logger.info('OAuth app updated', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
    })

    return this.toAppResponse(app)
  }

  /**
   * Delete OAuth app (soft delete)
   */
  async deleteApp(appId: string, orgId: string, deletedBy: string): Promise<void> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    app.isDeleted = true
    app.deletedBy = new Types.ObjectId(deletedBy)
    app.status = OAuthAppStatus.REVOKED
    await app.save()

    this.logger.info('OAuth app deleted', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
      deletedBy,
    })
  }

  /**
   * Regenerate client secret
   */
  async regenerateSecret(
    appId: string,
    orgId: string,
  ): Promise<OAuthAppWithSecret> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    const clientSecret = this.generateClientSecret()
    app.clientSecretEncrypted = this.encryptionService.encrypt(clientSecret)
    await app.save()

    this.logger.info('OAuth app secret regenerated', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
    })

    return {
      ...this.toAppResponse(app),
      clientSecret,
    }
  }

  /**
   * Suspend OAuth app
   */
  async suspendApp(appId: string, orgId: string): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    if (app.status === OAuthAppStatus.SUSPENDED) {
      throw new BadRequestError('OAuth app is already suspended')
    }

    app.status = OAuthAppStatus.SUSPENDED
    await app.save()

    this.logger.info('OAuth app suspended', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
    })

    return this.toAppResponse(app)
  }

  /**
   * Activate OAuth app
   */
  async activateApp(appId: string, orgId: string): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    if (app.status === OAuthAppStatus.REVOKED) {
      throw new BadRequestError('Cannot activate a revoked app')
    }

    if (app.status === OAuthAppStatus.ACTIVE) {
      throw new BadRequestError('OAuth app is already active')
    }

    app.status = OAuthAppStatus.ACTIVE
    await app.save()

    this.logger.info('OAuth app activated', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
    })

    return this.toAppResponse(app)
  }

  /**
   * Verify client credentials using timing-safe comparison
   * @see RFC 6749 Section 2.3 - Client Authentication
   */
  async verifyClientCredentials(
    clientId: string,
    clientSecret: string,
  ): Promise<IOAuthApp> {
    const app = await this.getAppByClientId(clientId)

    const storedSecret = this.encryptionService.decrypt(
      app.clientSecretEncrypted,
    )

    // Use timing-safe comparison to prevent timing attacks
    // Hash both secrets with SHA-256 to get fixed-length buffers for comparison
    const hash = (secret: string) =>
      crypto.createHash('sha256').update(secret).digest()

    const storedHash = hash(storedSecret)
    const providedHash = hash(clientSecret)

    if (!crypto.timingSafeEqual(storedHash, providedHash)) {
      throw new InvalidClientError('Invalid client credentials')
    }

    return app
  }

  /**
   * Validate redirect URI for an app
   */
  validateRedirectUriForApp(app: IOAuthApp, redirectUri: string): void {
    if (!app.redirectUris.includes(redirectUri)) {
      throw new InvalidRedirectUriError(
        'redirect_uri does not match any registered URIs',
        { providedUri: redirectUri },
      )
    }
  }

  /**
   * Check if grant type is allowed for app
   */
  isGrantTypeAllowed(app: IOAuthApp, grantType: string): boolean {
    return app.allowedGrantTypes.includes(grantType as OAuthGrantType)
  }

  private generateClientSecret(): string {
    return crypto.randomBytes(CLIENT_SECRET_LENGTH).toString('hex')
  }

  private validateRedirectUris(uris: string[]): void {
    for (const uri of uris) {
      try {
        const parsed = new URL(uri)
        // Allow localhost for development
        if (
          parsed.protocol !== 'https:' &&
          parsed.hostname !== 'localhost' &&
          parsed.hostname !== '127.0.0.1'
        ) {
          throw new InvalidRedirectUriError(
            `Redirect URI must use HTTPS (except localhost): ${uri}`,
          )
        }
        // Disallow fragments
        if (parsed.hash) {
          throw new InvalidRedirectUriError(
            `Redirect URI must not contain a fragment: ${uri}`,
          )
        }
      } catch (error) {
        if (error instanceof InvalidRedirectUriError) throw error
        throw new InvalidRedirectUriError(`Invalid redirect URI: ${uri}`)
      }
    }
  }

  private validateGrantTypes(grantTypes: string[]): void {
    const validGrantTypes = Object.values(OAuthGrantType)
    for (const grantType of grantTypes) {
      if (!validGrantTypes.includes(grantType as OAuthGrantType)) {
        throw new BadRequestError(`Invalid grant type: ${grantType}`)
      }
    }
  }

  private toAppResponse(app: IOAuthApp): OAuthAppResponse {
    return {
      id: (app._id as Types.ObjectId).toString(),
      slug: app.slug,
      clientId: app.clientId,
      name: app.name,
      description: app.description,
      redirectUris: app.redirectUris,
      allowedGrantTypes: app.allowedGrantTypes,
      allowedScopes: app.allowedScopes,
      status: app.status,
      logoUrl: app.logoUrl,
      homepageUrl: app.homepageUrl,
      privacyPolicyUrl: app.privacyPolicyUrl,
      termsOfServiceUrl: app.termsOfServiceUrl,
      isConfidential: app.isConfidential,
      accessTokenLifetime: app.accessTokenLifetime,
      refreshTokenLifetime: app.refreshTokenLifetime,
      createdAt: app.createdAt,
      updatedAt: app.updatedAt,
    }
  }
}
