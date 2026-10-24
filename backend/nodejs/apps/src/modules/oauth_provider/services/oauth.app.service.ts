import { injectable, inject } from 'inversify'
import { randomUUID } from 'crypto'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { ScopeValidatorService } from './scope.validator.service'
import { OAuthTokenService } from './oauth_token.service'
import {
  OAuthApp,
  IOAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../schema/oauth.app.schema'
import {
  sameKnowledgeLimit,
  toKnowledgeLimit,
} from '../schema/knowledge_limit.schema';
import {
  InvalidClientError,
  InvalidRedirectUriError,
} from '../../../libs/errors/oauth.errors'
import {
  NotFoundError,
  BadRequestError,
  ForbiddenError,
} from '../../../libs/errors/http.errors';
import { ADMIN_ACCESS_REQUIRED_MESSAGE } from '../../user_management/services/user-admin.service';
import { Users } from '../../user_management/schema/users.schema'
import {
  CreateOAuthAppRequest,
  UpdateOAuthAppRequest,
  OAuthAppResponse,
  OAuthAppWithSecret,
  ListAppsQuery,
  PaginatedResponse,
} from '../types/oauth.types'
import {
  ALLOWED_CUSTOM_REDIRECT_URIS,
  FIRST_PARTY_DEVICE_CLIENT_ID,
  PAT_APP_CLIENT_ID_PREFIX,
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
} from '../constants/constants'

const CLIENT_SECRET_LENGTH = 32

export const APP_WORKSPACE_MISMATCH_MESSAGE =
  'This application belongs to a different workspace. Switch to that workspace and try again.';

/**
 * An app issues tokens only for the workspace that created it. DCR apps and
 * the first-party device app are registered once per instance, so every
 * workspace may use them.
 */
export function isAppUsableInOrg(app: IOAuthApp, orgId: string): boolean {
  if (app.isDynamic === true || app.clientId === FIRST_PARTY_DEVICE_CLIENT_ID) {
    return true;
  }
  return app.orgId.toString() === orgId;
}

type WorkspaceMismatchDetails = (app: IOAuthApp) => Record<string, string>;

let workspaceMismatchDetails: WorkspaceMismatchDetails = () => ({});

/** Extra fields for the "app belongs to another workspace" answer; none by default. */
export function setWorkspaceMismatchDetails(
  fn: WorkspaceMismatchDetails,
): void {
  workspaceMismatchDetails = fn;
}

export function getWorkspaceMismatchDetails(
  app: IOAuthApp,
): Record<string, string> {
  return workspaceMismatchDetails(app);
}

@injectable()
export class OAuthAppService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EncryptionService') private encryptionService: EncryptionService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('OAuthTokenService')
    private oauthTokenService: OAuthTokenService,
  ) {}

  /**
   * Every org member (including org admins) only sees OAuth apps they created.
   * Matches compound index `{ orgId, createdBy, isDeleted, createdAt }` on `OAuthApp` for list queries.
   *
   * Excludes the per-org synthetic PAT app (`pat-system:<orgId>`, see
   * {@link PatService}) and the instance-wide first-party device app
   * (`pipeshub-agent`) — internal clients, not something their creator
   * should be able to view, edit, suspend, delete, or pull a working
   * secret for through this CRUD surface.
   */
  private buildAppFilter(orgId: string, userId: string): Record<string, unknown> {
    return {
      orgId: new Types.ObjectId(orgId),
      isDeleted: false,
      createdBy: new Types.ObjectId(userId),
      clientId: {
        $nin: [FIRST_PARTY_DEVICE_CLIENT_ID],
        $not: new RegExp(
          `^(?:${PAT_APP_CLIENT_ID_PREFIX}|${SERVICE_TOKEN_APP_CLIENT_ID_PREFIX})`,
        ),
      },
      isDynamic: { $ne: true },
    }
  }

  /**
   * Create a new OAuth app
   */
  async createApp(
    orgId: string,
    createdBy: string,
    isAdmin: boolean,
    data: CreateOAuthAppRequest,
  ): Promise<OAuthAppWithSecret> {
    // Validate scopes
    this.scopeValidatorService.validateRequestedScopes(
      data.allowedScopes,
      this.scopeValidatorService.getAllowedScopeNamesForRole(isAdmin),
    )

    // Validate grant types
    const allowedGrantTypes = data.allowedGrantTypes || [
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ]
    this.validateGrantTypes(allowedGrantTypes)

    // An app that allows client_credentials gets tokens with no person in
    // them. Without a service account to act as, it falls back to whoever
    // created it (`oauth.provider.controller.ts`, where the identity resolves
    // to `tokenIdentityUserId ?? createdBy`) — so an app created by an
    // administrator would carry administrator rights for as long as it lives,
    // with nothing on screen saying so.
    //
    // Required at creation rather than offered, because a safety step nobody
    // is asked about is one almost nobody takes. Only this grant needs it:
    // an authorization_code app acts as the person who authorised it.
    const needsServiceAccount = allowedGrantTypes.includes(
      OAuthGrantType.CLIENT_CREDENTIALS,
    );
    let tokenIdentityUserId: Types.ObjectId | undefined;
    if (needsServiceAccount) {
      // Choosing the service account decides whose documents this app's
      // tokens reach, so it is an administrator's decision wherever it is
      // made. Changing it later already requires one (`userAdminCheck` on
      // PUT /oauth-clients/:appId/token-identity); creating an app does not,
      // because any member may create an authorization_code app. Without this
      // a member could set at creation what they would be refused a minute
      // later — and knowing one service account id is enough, since the
      // lookup only checks the org, the kind and that it is enabled.
      if (!isAdmin) {
        throw new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
      }
      if (!data.tokenIdentityUserId) {
        throw new BadRequestError(
          'An application that uses client_credentials must name the service ' +
            'account its tokens act as. Without one it would act as whoever ' +
            'created it.',
        );
      }
      tokenIdentityUserId = await this.requireUsableServiceAccount(
        data.tokenIdentityUserId,
        orgId,
      );
    }

    const redirectUris = data.redirectUris || []
    this.validateRedirectUris(redirectUris)

    // Generate credentials
    const clientId = randomUUID()
    const clientSecret = this.generateClientSecret()
    const clientSecretEncrypted = this.encryptionService.encrypt(clientSecret)

    const app = await OAuthApp.create({
      clientId,
      clientSecretEncrypted,
      name: data.name,
      description: data.description,
      orgId: new Types.ObjectId(orgId),
      createdBy: new Types.ObjectId(createdBy),
      tokenIdentityUserId,
      redirectUris,
      allowedGrantTypes,
      allowedScopes: data.allowedScopes,
      homepageUrl: data.homepageUrl,
      privacyPolicyUrl: data.privacyPolicyUrl,
      termsOfServiceUrl: data.termsOfServiceUrl,
      isConfidential: data.isConfidential ?? true,
      accessTokenLifetime: data.accessTokenLifetime ?? 3600,
      refreshTokenLifetime: data.refreshTokenLifetime ?? 2592000,
      knowledgeLimit: data.knowledgeLimit,
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
   * The service account an app's own tokens may act as, or a refusal.
   *
   * `kind` is part of the query on purpose: a colleague's user id cannot be
   * used here to make an app's tokens act as them. Shared by creation and
   * re-binding so the two cannot drift on what counts as a valid target.
   */
  private async requireUsableServiceAccount(
    serviceAccountId: string,
    orgId: string,
  ): Promise<Types.ObjectId> {
    if (!Types.ObjectId.isValid(serviceAccountId)) {
      throw new NotFoundError(
        'That service account was not found in this organisation. Choose one ' +
          'from Workspace → Service accounts, or create one there first.',
      );
    }

    const serviceAccount = await Users.findOne({
      _id: serviceAccountId,
      orgId,
      kind: 'service',
      isDeleted: false,
    })
      .select('isDisabled')
      .lean()
      .exec();

    if (!serviceAccount) {
      throw new NotFoundError(
        'That service account was not found in this organisation. Choose one ' +
          'from Workspace → Service accounts, or create one there first.',
      );
    }
    if (serviceAccount.isDisabled === true) {
      throw new BadRequestError(
        'That service account is disabled. Enable it before pointing an application at it.',
      );
    }

    return new Types.ObjectId(serviceAccountId);
  }

  /**
   * Point an app's `client_credentials` tokens at a different service account.
   *
   * This is the way out of the problem the grant has: without it, a token
   * minted from an app acts as whoever created that app, carrying their
   * document access and their role, and stops working when they leave. Aimed
   * at a service account instead, it acts as an identity that exists for the
   * job, holds only what someone granted it, and survives any one person
   * leaving.
   *
   * Clearing it (`serviceAccountId: null`) is refused while the app allows
   * the grant, since that would put the tokens back on the creator — the
   * thing this exists to prevent. Only an app that does not allow the grant
   * can be cleared. An app that predates the requirement and still allows it
   * is refused too, even though it has no binding to remove — the carve-out
   * for those lives on `updateApp`, which lets them be edited, and not here.
   *
   * Revokes the tokens the app has already issued. They are not carried over
   * to the new identity: the middleware reads this field per request, and a
   * token whose `createdBy` claim disagrees with the record is refused with
   * 401 rather than honoured as the new account. So a token minted before
   * the change stops working, which is the point — anything still holding
   * one has to ask for another.
   *
   * The app's `createdBy` is untouched: that is who manages the app, and it
   * is what Developer Settings filters on. Moving it would leave the app
   * visible to nobody, since no person can sign in as a service account.
   */
  async setTokenIdentity(
    appId: string,
    orgId: string,
    userId: string,
    serviceAccountId: string | null,
  ): Promise<OAuthAppResponse> {
    if (!Types.ObjectId.isValid(appId)) {
      throw new NotFoundError('OAuth app not found')
    }
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
    })
    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    // Captured before anything is changed, so a failed revocation can put it
    // back rather than restoring the value it was just set to.
    const previousIdentity = app.tokenIdentityUserId

    if (serviceAccountId === null) {
      // Clearing it puts the tokens back on the app's creator, which is the
      // thing creation and the update path both exist to prevent. Refused
      // while the grant is allowed, so a binding this product now requires is
      // not one request away from being undone.
      //
      // Switching to a different service account stays allowed, and an app
      // that is already unbound never needs this call, so nothing legacy is
      // frozen by it.
      if (app.allowedGrantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
        throw new BadRequestError(
          'This application uses client_credentials, so it must act as a ' +
            'service account. Point it at a different one instead of ' +
            'clearing it; clearing it would put its tokens back on whoever ' +
            'created the application.',
        );
      }
      app.tokenIdentityUserId = undefined;
    } else {
      app.tokenIdentityUserId = await this.requireUsableServiceAccount(
        serviceAccountId,
        orgId,
      );
    }

    await app.save()

    // Every token this app has already issued was minted carrying the old
    // identity, and that claim is what the Python services read to decide
    // whose documents a request may reach. Leaving them alive would mean the
    // application went on acting as the previous identity until they expired
    // — the very situation an administrator makes this change to end.
    //
    // It is also the honest reading of what just happened: the application is
    // no longer the same principal, so its credentials should not be either.
    // Whatever uses it needs a new token.
    try {
      await this.oauthTokenService.revokeAllTokensForApp(app.clientId)
    } catch (error) {
      // The new identity is already committed, and every outstanding token
      // still carries the old one. Putting the identity back leaves the
      // application as it was rather than half-changed, and the caller is
      // told the change did not happen.
      app.tokenIdentityUserId = previousIdentity
      await app.save()
      throw error
    }

    this.logger.info('OAuth app token identity changed', {
      appId,
      orgId,
      changedBy: userId,
      serviceAccountId,
    })
    return this.toAppResponse(app)
  }

  /**
   * RFC 7591 Dynamic Client Registration.
   * Never accepts `client_credentials`. The grant carries no identity of
   * its own, so AuthMiddleware resolves its tokens to the app's
   * `createdBy` — they act as the person who registered the client, with
   * that person's documents and role. That is not an identity a client
   * registered dynamically, without review, should be able to obtain.
   */
  async createDynamicClient(params: {
    orgId: string
    createdBy: string
    name: string
    redirectUris: string[]
    allowedGrantTypes: OAuthGrantType[]
    allowedScopes: string[]
    isConfidential: boolean
    homepageUrl?: string
    privacyPolicyUrl?: string
    termsOfServiceUrl?: string
    logoUrl?: string
  }): Promise<OAuthAppWithSecret> {
    if (params.allowedGrantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
      throw new BadRequestError(
        'client_credentials is not allowed for dynamically registered clients',
      )
    }
    this.validateGrantTypes(params.allowedGrantTypes)
    this.validateDcrRedirectUris(params.redirectUris)

    const clientId = randomUUID()
    const clientSecret = this.generateClientSecret()
    const clientSecretEncrypted = this.encryptionService.encrypt(clientSecret)

    const app = await OAuthApp.create({
      clientId,
      clientSecretEncrypted,
      name: params.name,
      orgId: new Types.ObjectId(params.orgId),
      createdBy: new Types.ObjectId(params.createdBy),
      redirectUris: params.redirectUris,
      allowedGrantTypes: params.allowedGrantTypes,
      allowedScopes: params.allowedScopes,
      homepageUrl: params.homepageUrl,
      privacyPolicyUrl: params.privacyPolicyUrl,
      termsOfServiceUrl: params.termsOfServiceUrl,
      logoUrl: params.logoUrl,
      isConfidential: params.isConfidential,
      isDynamic: true,
      accessTokenLifetime: 3600,
      refreshTokenLifetime: 2592000,
    })

    this.logger.info('Dynamically registered OAuth client', {
      clientId,
      orgId: params.orgId,
      isConfidential: params.isConfidential,
    })

    return {
      ...this.toAppResponse(app),
      clientSecret,
    }
  }

  /**
   * Get OAuth app by ID
   */
  async getAppById(
    appId: string,
    orgId: string,
    userId: string,
  ): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
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
    userId: string,
    query: ListAppsQuery,
  ): Promise<PaginatedResponse<OAuthAppResponse>> {
    const page = query.page || 1
    const limit = query.limit || 20
    const skip = (page - 1) * limit

    const filter: Record<string, unknown> = this.buildAppFilter(orgId, userId)

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
    userId: string,
    isAdmin: boolean,
    data: UpdateOAuthAppRequest,
  ): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    // Validate scopes if provided
    if (data.allowedScopes) {
      this.scopeValidatorService.validateRequestedScopes(
        data.allowedScopes,
        this.scopeValidatorService.getAllowedScopeNamesForRole(isAdmin),
      )
    }

    // Validate redirect URIs if provided
    if (data.redirectUris) {
      this.validateRedirectUris(data.redirectUris);
    }

    // Validate grant types if provided
    if (data.allowedGrantTypes) {
      this.validateGrantTypes(data.allowedGrantTypes)

      // Creation requires a service account for client_credentials. Without
      // this, that requirement is one edit away from meaningless: make an
      // authorization_code app, then add the grant here and it falls back to
      // its creator again.
      //
      // Scoped to apps that are *gaining* the grant. An app that already has
      // it without a service account predates this rule, and refusing here
      // would mean it could not be renamed until it was migrated, which
      // punishes the wrong thing.
      const gainingClientCredentials =
        data.allowedGrantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS) &&
        !app.allowedGrantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS);

      // Gaining the grant is the same decision as making it at creation, and
      // this route has no admin check of its own, so without this the gate on
      // creation is bypassed by making an authorization_code app and editing
      // it afterwards.
      if (gainingClientCredentials && !isAdmin) {
        throw new ForbiddenError(ADMIN_ACCESS_REQUIRED_MESSAGE);
      }

      if (gainingClientCredentials && !app.tokenIdentityUserId) {
        throw new BadRequestError(
          'Point this application at a service account before allowing ' +
            'client_credentials. Without one its tokens would act as whoever ' +
            'created it.',
        );
      }

      // And that the account still works. One bound months ago may since have
      // been disabled or deleted, in which case the update would succeed and
      // every token request afterwards would fail — a confusing way to find
      // out, and the check is the same one creation uses.
      if (gainingClientCredentials && app.tokenIdentityUserId) {
        await this.requireUsableServiceAccount(
          app.tokenIdentityUserId.toString(),
          orgId,
        );
      }
    }

    const previousScopes = [...app.allowedScopes]
    const previousLimit = toKnowledgeLimit(app.knowledgeLimit);

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
    if (data.knowledgeLimit !== undefined) {
      app.knowledgeLimit = data.knowledgeLimit ?? undefined;
    }

    await app.save()

    // Tokens carry their scopes in the claim, and neither Node nor the Python
    // services compare that claim with the app on each request. Removing a
    // scope therefore only takes effect for tokens issued after the change.
    const removedScopes = previousScopes.filter(
      (scope) => !app.allowedScopes.includes(scope),
    )
    // The knowledge limit rides in the claim the same way. Any change goes:
    // a new limit may hide what a token reaches, and a lifted one widens what
    // users agreed to, so they connect again.
    const limitChanged = !sameKnowledgeLimit(app.knowledgeLimit, previousLimit);
    if (removedScopes.length > 0 || limitChanged) {
      try {
        await this.oauthTokenService.revokeAllTokensForApp(app.clientId)
      } catch (error) {
        app.allowedScopes = previousScopes
        app.knowledgeLimit = previousLimit;
        await app.save()
        throw error
      }
      this.logger.info('OAuth app access changed; tokens revoked', {
        appId: (app._id as Types.ObjectId).toString(),
        orgId,
        removedScopes,
        limitChanged,
      })
    }

    this.logger.info('OAuth app updated', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
    })

    return this.toAppResponse(app)
  }

  /**
   * Delete OAuth app (soft delete)
   */
  async deleteApp(
    appId: string,
    orgId: string,
    userId: string,
  ): Promise<void> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
    })

    if (!app) {
      throw new NotFoundError('OAuth app not found')
    }

    app.isDeleted = true
    app.deletedBy = new Types.ObjectId(userId)
    app.status = OAuthAppStatus.REVOKED
    await app.save()

    this.logger.info('OAuth app deleted', {
      appId: (app._id as Types.ObjectId).toString(),
      orgId,
      userId,
    })
  }

  /**
   * Regenerate client secret
   */
  async regenerateSecret(
    appId: string,
    orgId: string,
    userId: string,
  ): Promise<OAuthAppWithSecret> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
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
  async suspendApp(
    appId: string,
    orgId: string,
    userId: string,
  ): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
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
  async activateApp(
    appId: string,
    orgId: string,
    userId: string,
  ): Promise<OAuthAppResponse> {
    const app = await OAuthApp.findOne({
      _id: new Types.ObjectId(appId),
      ...this.buildAppFilter(orgId, userId),
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
      if (ALLOWED_CUSTOM_REDIRECT_URIS.includes(uri)) {
        continue;
      }
      try {
        const parsed = new URL(uri)
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

  /**
   * DCR redirect URIs: HTTPS, loopback HTTP, or private-use schemes (RFC 8252).
   * `javascript:` / `data:` / `file:` / `vbscript:` are rejected.
   */
  validateDcrRedirectUris(uris: string[]): void {
    const blocked = new Set(['javascript:', 'data:', 'file:', 'vbscript:'])
    for (const uri of uris) {
      let parsed: URL
      try {
        parsed = new URL(uri)
      } catch {
        throw new InvalidRedirectUriError(`Invalid redirect URI: ${uri}`)
      }
      if (parsed.hash) {
        throw new InvalidRedirectUriError(
          `Redirect URI must not contain a fragment: ${uri}`,
        )
      }
      if (blocked.has(parsed.protocol)) {
        throw new InvalidRedirectUriError(
          `Redirect URI scheme is not allowed: ${uri}`,
        )
      }
      if (parsed.protocol === 'http:') {
        const host = parsed.hostname.toLowerCase()
        if (host !== 'localhost' && host !== '127.0.0.1' && host !== '[::1]' && host !== '::1') {
          throw new InvalidRedirectUriError(
            `http redirect URIs are only allowed on loopback: ${uri}`,
          )
        }
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
      knowledgeLimit: toKnowledgeLimit(app.knowledgeLimit),
      createdAt: app.createdAt,
      updatedAt: app.updatedAt,
    }
  }
}
