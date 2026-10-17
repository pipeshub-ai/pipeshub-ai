import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Types } from 'mongoose'
import { Logger } from '../../../libs/services/logger.service'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { Org } from '../../user_management/schema/org.schema'
import { Users } from '../../user_management/schema/users.schema'
import {
  OAuthApp,
  IOAuthApp,
  OAuthAppStatus,
  OAuthGrantType,
} from '../schema/oauth.app.schema'
import { AgentMcpScopes } from '../config/scopes.config'
import { ScopeValidatorService } from './scope.validator.service'
import { AppConfig } from '../../tokens_manager/config/config'
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../constants/constants'

const CLIENT_SECRET_BYTES = 32

/**
 * Ensures the official "PipesHub agent" OAuth app exists so coding agents
 * can start RFC 8628 device login without DCR and without an admin creating
 * an OAuth app. One public client_id per instance; many agents share it.
 * Tokens still carry the person who clicked Allow.
 *
 * Never grants `client_credentials`. Created lazily (first discovery or
 * first device_authorization with this client_id) so upgrades of existing
 * orgs get the app without re-running first-run.
 */
@injectable()
export class FirstPartyDeviceAppService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('EncryptionService') private encryptionService: EncryptionService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

  /**
   * @returns the well-known client_id, or null when the instance has no org
   * yet or the agent preset cannot be granted under current MCP_SCOPES.
   */
  async getOrCreate(): Promise<string | null> {
    const existing = await OAuthApp.findOne({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
    })
    if (existing) {
      return existing.clientId
    }

    const owner = await this.resolveOwner()
    if (!owner) {
      return null
    }

    const allowedScopes = this.resolveScopes()
    if (allowedScopes.length === 0) {
      this.logger.warn(
        'First-party device app not created: agent preset is empty under MCP_SCOPES',
      )
      return null
    }

    try {
      const app = await this.createApp(owner.orgId, owner.createdBy, allowedScopes)
      this.logger.info('First-party device app created', {
        clientId: app.clientId,
        orgId: owner.orgId,
      })
      return app.clientId
    } catch (error) {
      const winner = await OAuthApp.findOne({
        clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      })
      if (winner) {
        return winner.clientId
      }
      throw error
    }
  }

  private async createApp(
    orgId: string,
    createdBy: string,
    allowedScopes: string[],
  ): Promise<IOAuthApp> {
    const clientSecret = crypto.randomBytes(CLIENT_SECRET_BYTES).toString('hex')
    return OAuthApp.create({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      // Public client: device poll uses token_endpoint_auth_method none.
      // Schema still requires a stored secret; it is never used to authenticate.
      clientSecretEncrypted: this.encryptionService.encrypt(clientSecret),
      name: 'PipesHub agent',
      description:
        'Official coding-agent device login for this instance. Public client; ' +
        'device_code and refresh_token only. Not a third-party app and not ' +
        'created through dynamic client registration.',
      orgId: new Types.ObjectId(orgId),
      createdBy: new Types.ObjectId(createdBy),
      redirectUris: [],
      allowedGrantTypes: [OAuthGrantType.DEVICE_CODE, OAuthGrantType.REFRESH_TOKEN],
      allowedScopes,
      isConfidential: false,
      isDynamic: false,
      status: OAuthAppStatus.ACTIVE,
    })
  }

  private resolveScopes(): string[] {
    const mcp = new Set(this.appConfig.mcpScopes || [])
    const memberAllowed = new Set(
      this.scopeValidatorService.getAllowedScopeNamesForRole(false),
    )
    return AgentMcpScopes.filter((scope) => {
      if (!memberAllowed.has(scope)) {
        return false
      }
      if (mcp.size > 0 && !mcp.has(scope)) {
        return false
      }
      return true
    })
  }

  private async resolveOwner(): Promise<{
    orgId: string
    createdBy: string
  } | null> {
    const orgs = await Org.find({ isDeleted: { $ne: true } })
      .sort({ createdAt: 1 })
      .select('_id')
      .lean()
    const org = orgs[0]
    if (!org) {
      return null
    }
    const user = await Users.findOne({
      orgId: org._id,
      isDeleted: { $ne: true },
    })
      .sort({ createdAt: 1 })
      .select('_id')
      .lean()
    if (!user?._id) {
      return null
    }
    return { orgId: org._id.toString(), createdBy: user._id.toString() }
  }
}
