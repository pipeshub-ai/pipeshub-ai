import { inInstallOrg } from '../../../libs/repositories/identity/install-org';
import { injectable, inject } from 'inversify'
import crypto from 'crypto'
import { Logger } from '../../../libs/services/logger.service'
import { EncryptionService } from '../../../libs/encryptor/encryptor'
import { getUserRepository } from '../../../libs/repositories/user'
import {
  getOAuthAppRepository,
  OAuthAppStatus,
  OAuthGrantType,
  type OAuthAppDto,
} from '../../../libs/repositories/oauth-app'
import { getIdentityPort } from '../../../libs/repositories/identity'
import { AgentMcpScopes } from '../config/scopes.config'
import { ScopeValidatorService } from './scope.validator.service'
import { AppConfig } from '../../tokens_manager/config/config'
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../constants/constants'
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

const CLIENT_SECRET_BYTES = 32

// A refresh token is minted only for offline_access, so without it a device
// login, refresh_token grant and all, would end with its one-hour access token.
const DEVICE_APP_SCOPES = [...AgentMcpScopes, OAuthScopeNames.OFFLINE_ACCESS];

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
    // Discovery and the device endpoint arrive with no session: the server itself runs this as the install's organisation.
    return inInstallOrg('oauth.first_party_device', () => this.ensure(), null);
  }

  private async ensure(): Promise<string | null> {
    const repo = getOAuthAppRepository()
    const existing = await repo.findByClientId(FIRST_PARTY_DEVICE_CLIENT_ID)
    if (existing) {
      await this.addNewPresetScopes(existing);
      return existing.clientId;
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
      const winner = await repo.findByClientId(FIRST_PARTY_DEVICE_CLIENT_ID)
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
  ): Promise<OAuthAppDto> {
    const clientSecret = crypto.randomBytes(CLIENT_SECRET_BYTES).toString('hex')
    return getOAuthAppRepository().create({
      clientId: FIRST_PARTY_DEVICE_CLIENT_ID,
      // Public client: device poll uses token_endpoint_auth_method none.
      // Schema still requires a stored secret; it is never used to authenticate.
      clientSecretEncrypted: this.encryptionService.encrypt(clientSecret),
      name: 'PipesHub agent',
      description:
        'Official coding-agent device login for this instance. Public client; ' +
        'device_code and refresh_token only. Not a third-party app and not ' +
        'created through dynamic client registration.',
      orgId,
      createdBy,
      redirectUris: [],
      allowedGrantTypes: [OAuthGrantType.DEVICE_CODE, OAuthGrantType.REFRESH_TOKEN],
      allowedScopes,
      isConfidential: false,
      isDynamic: false,
      status: OAuthAppStatus.ACTIVE,
    })
  }

  /**
   * The app is created once, so a scope added to the agent preset later never
   * reaches it on its own and device logins keep minting tokens without it.
   * Add what the preset grants now; never remove a scope, and leave tokens
   * already issued alone (the next consent picks the new scopes up).
   */
  private async addNewPresetScopes(app: OAuthAppDto): Promise<void> {
    const held = new Set(app.allowedScopes);
    const missing = this.resolveScopes().filter((scope) => !held.has(scope));
    if (missing.length === 0) {
      return;
    }
    try {
      await getOAuthAppRepository().addAllowedScopes(app._id, missing);
      this.logger.info('First-party device app gained new preset scopes', {
        clientId: app.clientId,
        scopes: missing,
      });
    } catch (error) {
      // Discovery must keep working; the next call retries the update.
      this.logger.warn('First-party device app scope update failed', {
        clientId: app.clientId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  private resolveScopes(): string[] {
    const mcp = new Set(this.appConfig.mcpScopes || [])
    const memberAllowed = new Set(
      this.scopeValidatorService.getAllowedScopeNamesForRole(false),
    )
    return DEVICE_APP_SCOPES.filter((scope) => {
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
    const orgId = await getIdentityPort().oldestOrgId()
    if (!orgId) {
      return null
    }
    const oldestUserId = await getUserRepository().getOldestId(orgId)
    if (!oldestUserId) {
      return null
    }
    return { orgId, createdBy: oldestUserId }
  }
}
