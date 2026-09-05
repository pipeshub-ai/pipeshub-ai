import { injectable, inject } from 'inversify'
import { Logger } from '../../../libs/services/logger.service'
import { BadRequestError, ForbiddenError } from '../../../libs/errors/http.errors'
import { InvalidScopeError } from '../../../libs/errors/oauth.errors'
import { Org } from '../../user_management/schema/org.schema'
import { Users } from '../../user_management/schema/users.schema'
import { OAuthGrantType } from '../schema/oauth.app.schema'
import { OAuthAppService } from './oauth.app.service'
import { ScopeValidatorService } from './scope.validator.service'
import { AgentMcpScopes } from '../config/scopes.config'
import { AppConfig } from '../../tokens_manager/config/config'

export interface DcrRequestBody {
  redirect_uris?: string[]
  token_endpoint_auth_method?: 'none' | 'client_secret_basic' | 'client_secret_post'
  grant_types?: string[]
  response_types?: string[]
  client_name?: string
  client_uri?: string
  logo_uri?: string
  scope?: string
  tos_uri?: string
  policy_uri?: string
}

export interface DcrResponse {
  client_id: string
  client_secret?: string
  client_id_issued_at: number
  client_secret_expires_at: number
  redirect_uris: string[]
  grant_types: string[]
  token_endpoint_auth_method: string
  client_name: string
  scope: string
}

@injectable()
export class OAuthDcrService {
  constructor(
    @inject('Logger') private logger: Logger,
    @inject('OAuthAppService') private oauthAppService: OAuthAppService,
    @inject('ScopeValidatorService')
    private scopeValidatorService: ScopeValidatorService,
    @inject('AppConfig') private appConfig: AppConfig,
  ) {}

  async register(body: DcrRequestBody): Promise<DcrResponse> {
    if (process.env.PIPESHUB_ENABLE_DCR === 'false') {
      throw new ForbiddenError('dynamic client registration is disabled')
    }

    const grantTypes = this.parseGrantTypes(body.grant_types)
    if (grantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
      throw new BadRequestError(
        'client_credentials is not allowed for dynamically registered clients',
      )
    }

    const redirectUris = body.redirect_uris ?? []
    const needsRedirect = grantTypes.includes(OAuthGrantType.AUTHORIZATION_CODE)
    if (needsRedirect && redirectUris.length < 1) {
      throw new BadRequestError(
        'redirect_uris is required when authorization_code is requested',
      )
    }

    const isConfidential =
      (body.token_endpoint_auth_method ?? 'none') !== 'none'
    const tokenEndpointAuthMethod = body.token_endpoint_auth_method ?? 'none'

    const { orgId, createdBy } = await this.resolveRegistrationOrg()
    const allowedScopes = this.resolveScopes(body.scope)

    const created = await this.oauthAppService.createDynamicClient({
      orgId,
      createdBy,
      name: body.client_name || 'Dynamically registered client',
      redirectUris,
      allowedGrantTypes: grantTypes,
      allowedScopes,
      isConfidential,
      homepageUrl: body.client_uri,
      privacyPolicyUrl: body.policy_uri,
      termsOfServiceUrl: body.tos_uri,
      logoUrl: body.logo_uri,
    })

    const issuedAt = Math.floor(Date.now() / 1000)
    const response: DcrResponse = {
      client_id: created.clientId,
      client_id_issued_at: issuedAt,
      client_secret_expires_at: 0,
      redirect_uris: created.redirectUris,
      grant_types: created.allowedGrantTypes,
      token_endpoint_auth_method: tokenEndpointAuthMethod,
      client_name: created.name,
      scope: created.allowedScopes.join(' '),
    }
    if (isConfidential) {
      response.client_secret = created.clientSecret
    }
    return response
  }

  private parseGrantTypes(raw?: string[]): OAuthGrantType[] {
    if (!raw || raw.length === 0) {
      return [OAuthGrantType.AUTHORIZATION_CODE, OAuthGrantType.REFRESH_TOKEN]
    }
    const allowed = new Set<string>(Object.values(OAuthGrantType))
    const parsed: OAuthGrantType[] = []
    for (const g of raw) {
      if (!allowed.has(g)) {
        throw new BadRequestError(`unsupported grant_type: ${g}`)
      }
      parsed.push(g as OAuthGrantType)
    }
    return parsed
  }

  private resolveScopes(scope?: string): string[] {
    const mcp = new Set(this.appConfig.mcpScopes || [])
    const memberAllowed = new Set(
      this.scopeValidatorService.getAllowedScopeNamesForRole(false),
    )
    const requested = scope
      ? this.scopeValidatorService.parseScopes(scope)
      : [...AgentMcpScopes]

    const granted: string[] = []
    for (const s of requested) {
      if (!memberAllowed.has(s)) {
        throw new InvalidScopeError(`scope is not allowed: ${s}`)
      }
      if (mcp.size > 0 && !mcp.has(s)) {
        throw new InvalidScopeError(`scope is not in MCP_SCOPES: ${s}`)
      }
      granted.push(s)
    }
    if (granted.length === 0) {
      throw new InvalidScopeError('at least one scope is required')
    }
    return granted
  }

  private async resolveRegistrationOrg(): Promise<{
    orgId: string
    createdBy: string
  }> {
    const orgs = await Org.find({ isDeleted: { $ne: true } })
      .sort({ createdAt: 1 })
      .select('_id')
      .lean()
    if (orgs.length === 0) {
      throw new BadRequestError('Instance is not initialized')
    }
    const orgId = orgs[0]._id.toString()
    const user = await Users.findOne({
      orgId: orgs[0]._id,
      isDeleted: { $ne: true },
    })
      .sort({ createdAt: 1 })
      .select('_id')
      .lean()
    if (!user) {
      throw new BadRequestError('Instance has no users')
    }
    this.logger.info('DCR bound to instance org', { orgId })
    return { orgId, createdBy: user._id.toString() }
  }
}
