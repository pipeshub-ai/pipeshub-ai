import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { ConfigService } from '../../tokens_manager/services/cm.service'
import { OAuthTokenService } from './oauth_token.service'
import { ScopeValidatorService } from './scope.validator.service'
import {
  OAuthApp,
  IOAuthApp,
  OAuthAppStatus,
} from '../schema/oauth.app.schema'
import { NotFoundError } from '../../../libs/errors/http.errors'
import {
  AdminPatListItem,
  CreatePatRequest,
  PaginatedResponse,
  PatListItem,
  PatWithSecret,
} from '../types/oauth.types'
import { PAT_TOKEN_PREFIX, PAT_APP_CLIENT_ID_PREFIX } from '../constants/constants'
import { Users } from '../../user_management/schema/users.schema'

const CLIENT_SECRET_BYTES = 32
const SECONDS_PER_DAY = 86400
// Short-lived by default: a token minted without an explicit expiryDays
// (e.g. a direct API call rather than the UI) should not silently get the
// longest lifetime.
const DEFAULT_EXPIRY_DAYS = 30
// OAuthAccessToken.expiresAt is required and TTL-indexed, so there's no
// real "never expires" value to store — a 100-year lifetime is the
// practical stand-in for the UI's "never" expiry option.
const NEVER_EXPIRES_DAYS = 365 * 100

function patClientId(orgId: string): string {
  return `${PAT_APP_CLIENT_ID_PREFIX}${orgId}`
}

/**
 * Issues, lists, and revokes personal access tokens (PATs) — long-lived
 * credentials a user mints themselves for programmatic access (e.g. the
 * MCP server), as opposed to the 24h session JWT or an admin-managed
 * OAuth app.
 *
 * PATs are OAuth access tokens: every capability here (signing, hashing,
 * revocation, the `/mcp` auth path) is reused unchanged from
 * {@link OAuthTokenService} rather than parallel token machinery. The one
 * gap is that `OAuthTokenService.generateTokens` requires a real
 * `IOAuthApp` (it reads `clientId`/`createdBy` off it), but a PAT isn't
 * issued to a third-party app — so each org gets one lazily-created,
 * synthetic "Personal Access Tokens" app that every PAT in that org is
 * minted against. That keeps `OAuthAccessToken.clientId` a real,
 * non-nullable value (unchanged for every other consumer of that schema)
 * instead of relaxing the field.
 */
@injectable()
export class PatService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EncryptionService') private encryptionService: EncryptionService,
    @inject('ConfigService') private configService: ConfigService,
    @inject('OAuthTokenService') private oauthTokenService: OAuthTokenService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
  ) {}

  /**
   * The scope set a new PAT defaults to — read from `MCP_SCOPES` (via
   * `ConfigService`) rather than hardcoded, so a deployment that
   * customizes that env var stays consistent with what the UI offers.
   */
  async getDefaultScopes(): Promise<string[]> {
    return this.configService.getMcpScopes()
  }

  private async getOrCreatePatApp(
    orgId: string,
    actingUserId: string,
  ): Promise<IOAuthApp> {
    const clientId = patClientId(orgId)
    const existing = await OAuthApp.findOne({ clientId })
    if (existing) {
      return existing
    }

    const mcpScopes = await this.configService.getMcpScopes()
    const clientSecret = crypto.randomBytes(CLIENT_SECRET_BYTES).toString('hex')

    try {
      return await OAuthApp.create({
        clientId,
        // Never used to authenticate anything — PATs don't go through the
        // /token endpoint's client-credential checks — but the schema
        // requires a value, so this is generated and immediately discarded.
        clientSecretEncrypted: this.encryptionService.encrypt(clientSecret),
        name: 'Personal Access Tokens',
        description:
          'Internal pseudo-client that owns user-issued personal access ' +
          'tokens for this org. Not a registered OAuth app: never used ' +
          'through the authorize or token-exchange endpoints.',
        orgId: new Types.ObjectId(orgId),
        createdBy: new Types.ObjectId(actingUserId),
        redirectUris: [],
        allowedGrantTypes: [],
        allowedScopes: mcpScopes,
        isConfidential: true,
        status: OAuthAppStatus.ACTIVE,
      })
    } catch (error) {
      // Two users creating an org's first PAT concurrently can both pass
      // the findOne above; the unique clientId index fails the loser's
      // create() — fetch what the winner just inserted instead of erroring.
      const createdByWinner = await OAuthApp.findOne({ clientId })
      if (createdByWinner) {
        return createdByWinner
      }
      throw error
    }
  }

  private resolveLifetimeSeconds(expiryDays: 30 | 90 | 365 | 'never'): number {
    const days = expiryDays === 'never' ? NEVER_EXPIRES_DAYS : expiryDays
    return days * SECONDS_PER_DAY
  }

  /**
   * Mint a new personal access token for *userId*. Returns the raw token
   * once — callers must not attempt to retrieve it again (only the hash
   * is stored, matching every other OAuth access token).
   */
  async createToken(
    orgId: string,
    userId: string,
    fullName: string | undefined,
    request: CreatePatRequest,
  ): Promise<PatWithSecret> {
    const mcpScopes = await this.configService.getMcpScopes()
    const scopes =
      request.scopes && request.scopes.length > 0 ? request.scopes : mcpScopes
    this.scopeValidatorService.validateScopesForApp(scopes, mcpScopes)

    const app = await this.getOrCreatePatApp(orgId, userId)
    const lifetimeSeconds = this.resolveLifetimeSeconds(
      request.expiryDays ?? DEFAULT_EXPIRY_DAYS,
    )

    const tokens = await this.oauthTokenService.generateTokens(
      app,
      userId,
      orgId,
      scopes,
      false, // PATs are the long-lived credential themselves — no refresh token
      fullName,
      undefined,
      {
        accessTokenLifetimeOverrideSeconds: lifetimeSeconds,
        name: request.name,
      },
    )

    this.logger.info('Personal access token created', {
      orgId,
      userId,
      name: request.name,
      scopes,
      expiryDays: request.expiryDays ?? DEFAULT_EXPIRY_DAYS,
    })

    return {
      id: tokens.accessTokenId,
      name: request.name,
      scopes,
      createdAt: new Date(),
      expiresAt: new Date(Date.now() + lifetimeSeconds * 1000),
      // Prefixed so the raw token is grep-able / recognizable to secret
      // scanners, unlike a bare JWT. verifyAccessToken strips this back
      // off before hashing/verifying, so it's display-only.
      accessToken: `${PAT_TOKEN_PREFIX}${tokens.accessToken}`,
    }
  }

  /**
   * List *userId*'s own active (non-revoked, unexpired) personal access
   * tokens. Returns an empty list without creating the org's PAT app if
   * the user has never minted one.
   */
  async listTokens(orgId: string, userId: string): Promise<PatListItem[]> {
    const clientId = patClientId(orgId)
    const app = await OAuthApp.findOne({ clientId })
    if (!app) {
      return []
    }

    const tokens = await this.oauthTokenService.listAccessTokensForUser(
      clientId,
      userId,
    )
    return tokens.map((t) => ({
      id: t.id,
      name: t.name ?? '(unnamed token)',
      scopes: t.scopes,
      createdAt: t.createdAt,
      expiresAt: t.expiresAt,
      lastUsedAt: t.lastUsedAt,
    }))
  }

  /**
   * Revoke one of *userId*'s own personal access tokens by id.
   * @throws NotFoundError if the org has no PAT app yet, or the token
   *   doesn't exist / isn't owned by *userId* / is already revoked.
   */
  async revokeToken(
    orgId: string,
    userId: string,
    tokenId: string,
    reason?: string,
  ): Promise<void> {
    const clientId = patClientId(orgId)
    const app = await OAuthApp.findOne({ clientId })
    if (!app) {
      throw new NotFoundError('Personal access token not found')
    }

    const revoked = await this.oauthTokenService.revokeAccessTokenById(
      tokenId,
      clientId,
      userId,
      userId,
      reason ?? 'Revoked by owner',
    )
    if (!revoked) {
      throw new NotFoundError('Personal access token not found')
    }

    this.logger.info('Personal access token revoked', {
      orgId,
      userId,
      tokenId,
    })
  }

  /**
   * List every active personal access token in the org, across all
   * users — for org admins doing incident response (departed employee,
   * compromised laptop) on a credential only its owner could otherwise
   * see or revoke. Paginated (unlike the per-user `listTokens`) since an
   * org can have far more than a fixed-window cap's worth of active
   * PATs. Returns an empty page without creating the org's PAT app if
   * nobody has minted one yet.
   */
  async listAllTokens(
    orgId: string,
    page = 1,
    limit = 100,
  ): Promise<PaginatedResponse<AdminPatListItem>> {
    const clientId = patClientId(orgId)
    const app = await OAuthApp.findOne({ clientId })
    if (!app) {
      return { data: [], pagination: { page, limit, total: 0, totalPages: 0 } }
    }

    const { tokens, total } =
      await this.oauthTokenService.listAccessTokensForClientPaginated(
        clientId,
        page,
        limit,
      )

    // Deliberately not filtered by isDeleted — a departed employee's
    // token is exactly the case this endpoint exists for, and it must
    // still show up (with ownerDeleted: true) rather than silently drop
    // the owner's name/email from the response.
    const owners = await Users.find({
      _id: { $in: tokens.map((t) => t.userId) },
    })
      .select('email fullName isDeleted')
      .lean()
      .exec()
    const ownersById = new Map(owners.map((u) => [u._id.toString(), u]))

    const data = tokens.map((t): AdminPatListItem => {
      const owner = t.userId ? ownersById.get(t.userId) : undefined
      return {
        id: t.id,
        name: t.name ?? '(unnamed token)',
        scopes: t.scopes,
        createdAt: t.createdAt,
        expiresAt: t.expiresAt,
        lastUsedAt: t.lastUsedAt,
        userId: t.userId ?? '',
        ownerEmail: owner?.email,
        ownerFullName: owner?.fullName,
        ownerDeleted: !owner || Boolean(owner.isDeleted),
      }
    })

    return {
      data,
      pagination: { page, limit, total, totalPages: Math.ceil(total / limit) },
    }
  }

  /**
   * Revoke any user's personal access token in the org by id — the
   * admin counterpart to {@link revokeToken}, not scoped to a single
   * owner.
   * @throws NotFoundError if the org has no PAT app yet, or the token
   *   doesn't exist in this org / is already revoked.
   */
  async adminRevokeToken(
    orgId: string,
    adminUserId: string,
    tokenId: string,
    reason?: string,
  ): Promise<void> {
    const clientId = patClientId(orgId)
    const app = await OAuthApp.findOne({ clientId })
    if (!app) {
      throw new NotFoundError('Personal access token not found')
    }

    const revoked = await this.oauthTokenService.revokeAccessTokenByIdForClient(
      tokenId,
      clientId,
      adminUserId,
      reason ?? 'Revoked by org admin',
    )
    if (!revoked) {
      throw new NotFoundError('Personal access token not found')
    }

    this.logger.info('Personal access token revoked by admin', {
      orgId,
      adminUserId,
      tokenId,
    })
  }
}
