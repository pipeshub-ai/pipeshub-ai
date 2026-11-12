import { forOrg } from '../../../libs/context/orgContext';
import { injectable, inject } from 'inversify'
import { Logger } from '../../../libs/services/logger.service'
import { BadRequestError, ForbiddenError } from '../../../libs/errors/http.errors'
import { InvalidClientError, InvalidScopeError } from '../../../libs/errors/oauth.errors';
import { getIdentityPort } from '../../../libs/repositories/identity'
import { getUserRepository } from '../../../libs/repositories/user'
import {
  DYNAMIC_ACCESS_TOKEN_LIFETIME,
  DYNAMIC_REFRESH_TOKEN_LIFETIME,
  OAuthAppService,
} from './oauth.app.service'
import { ScopeValidatorService } from './scope.validator.service'
import { AgentMcpScopes, mcpDiscoveryScopes } from '../config/scopes.config';
import { AppConfig } from '../../tokens_manager/config/config'
import {
  getOAuthAppRepository,
  OAuthAppStatus,
  OAuthGrantType,
  type OAuthAppDto,
} from '../../../libs/repositories/oauth-app';
import {
  cimdEnabled,
  fetchClientMetadataDocument,
  isClientIdMetadataUrl,
} from './client_metadata_document';

/** The client of an authorization request, and how to keep it once the person consents. */
export interface AuthorizeClient {
  app: OAuthAppDto;
  /** Stores a metadata document client seen for the first time; the app as it is otherwise. */
  store(): Promise<OAuthAppDto>;
}

type NewDynamicClient = Parameters<OAuthAppService['createDynamicClient']>[0];

const sameMembers = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && a.every((x) => b.includes(x));

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
  response_types: string[]
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
    if (process.env.PIPESHUB_ENABLE_DCR !== 'true') {
      throw new ForbiddenError('dynamic client registration is disabled')
    }

    const grantTypes = this.parseGrantTypes(body.grant_types)
    if (grantTypes.includes(OAuthGrantType.CLIENT_CREDENTIALS)) {
      throw new BadRequestError(
        'client_credentials is not allowed for dynamically registered clients',
      )
    }

    const responseTypes = this.parseResponseTypes(
      body.response_types,
      grantTypes,
    )

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
      response_types: responseTypes,
      token_endpoint_auth_method: tokenEndpointAuthMethod,
      client_name: created.name,
      scope: created.allowedScopes.join(' '),
    }
    if (isConfidential) {
      response.client_secret = created.clientSecret
    }
    return response
  }

  /**
   * The client an authorization request names. A client id that is an https URL is a Client ID
   * Metadata Document: it is fetched on every authorization request, so the consent page and the
   * redirect check follow the document as it is now.
   *
   * A metadata client seen for the first time is not stored until the person consents:
   * `store()` stores it, so the token endpoints find it like any other client. A stored one gets
   * its scopes and grants re-derived from the document and the instance's limits each time,
   * so narrowing either takes effect at the next authorization and its refresh.
   * shortcut: the stored name and links keep their first fetch (they are overlaid from the
   * fresh document here); store them too if something reads them off the stored app.
   */
  async resolveAuthorizeClient(clientId: string): Promise<AuthorizeClient> {
    if (!cimdEnabled() || !isClientIdMetadataUrl(clientId)) {
      const app = await this.oauthAppService.getAppByClientId(clientId);
      return { app, store: () => Promise.resolve(app) };
    }
    const doc = await fetchClientMetadataDocument(clientId);
    // The redirect check compares against these, so they get the same checks as at registration.
    this.oauthAppService.validateDcrRedirectUris(doc.redirect_uris);
    const supported = new Set<string>([
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ]);
    // The same default as registration: without refresh_token, the refresh token offline_access issues could not be used.
    const allowedGrantTypes = (
      doc.grant_types ?? [
        OAuthGrantType.AUTHORIZATION_CODE,
        OAuthGrantType.REFRESH_TOKEN,
      ]
    ).filter((g): g is OAuthGrantType => supported.has(g));
    if (!allowedGrantTypes.includes(OAuthGrantType.AUTHORIZATION_CODE)) {
      throw new InvalidClientError(
        'The client metadata document must allow the authorization_code grant',
      );
    }
    // A metadata document rarely names a scope, and its client cannot register one, so it gets what the /mcp tools use (with offline_access, for a refresh token).
    const allowedScopes = doc.scope
      ? this.resolveScopes(doc.scope)
      : this.mcpToolScopes();
    const fresh = {
      name: (doc.client_name || new URL(clientId).host).slice(0, 100),
      redirectUris: doc.redirect_uris,
      homepageUrl: doc.client_uri,
      privacyPolicyUrl: doc.policy_uri,
      termsOfServiceUrl: doc.tos_uri,
      logoUrl: doc.logo_uri,
      allowedScopes,
      allowedGrantTypes,
    };

    if (await getOAuthAppRepository().findLiveByClientId(clientId)) {
      const stored = await this.oauthAppService.getAppByClientId(clientId);
      if (
        !sameMembers(stored.allowedScopes, allowedScopes) ||
        !sameMembers(stored.allowedGrantTypes, allowedGrantTypes)
      ) {
        await getOAuthAppRepository().setDynamicGrant(
          stored.orgId,
          stored._id,
          {
            allowedScopes,
            allowedGrantTypes,
          },
        );
      }
      const app = { ...stored, ...fresh };
      return { app, store: () => Promise.resolve(app) };
    }

    const now = new Date();
    const unsaved: OAuthAppDto = {
      _id: '',
      slug: '',
      clientId,
      clientSecretEncrypted: '',
      orgId: '',
      createdBy: '',
      status: OAuthAppStatus.ACTIVE,
      isConfidential: false,
      isDynamic: true,
      accessTokenLifetime: DYNAMIC_ACCESS_TOKEN_LIFETIME,
      refreshTokenLifetime: DYNAMIC_REFRESH_TOKEN_LIFETIME,
      isDeleted: false,
      createdAt: now,
      updatedAt: now,
      __v: 0,
      ...fresh,
    };
    return {
      app: unsaved,
      store: () => this.storeMetadataClient(clientId, fresh),
    };
  }

  private async storeMetadataClient(
    clientId: string,
    fresh: Omit<
      NewDynamicClient,
      'clientId' | 'orgId' | 'createdBy' | 'isConfidential'
    >,
  ): Promise<OAuthAppDto> {
    const { orgId, createdBy } = await this.resolveRegistrationOrg();
    try {
      await this.oauthAppService.createDynamicClient({
        ...fresh,
        clientId,
        orgId,
        createdBy,
        isConfidential: false,
      });
      this.logger.info('Stored client from its metadata document', {
        clientId,
      });
    } catch (error) {
      // Two first consents at once: the other one stored it. Anything else is a real failure.
      if (!(await getOAuthAppRepository().findLiveByClientId(clientId))) {
        throw error;
      }
    }
    return this.oauthAppService.getAppByClientId(clientId);
  }

  private mcpToolScopes(): string[] {
    const memberAllowed = new Set(
      this.scopeValidatorService.getAllowedScopeNamesForRole(false),
    );
    const granted = mcpDiscoveryScopes(this.appConfig.mcpScopes).filter((s) =>
      memberAllowed.has(s),
    );
    return granted.length > 0 ? granted : this.resolveScopes();
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

  private parseResponseTypes(
    raw: string[] | undefined,
    grantTypes: OAuthGrantType[],
  ): string[] {
    const hasAuthCode = grantTypes.includes(OAuthGrantType.AUTHORIZATION_CODE)
    if (!raw || raw.length === 0) {
      return hasAuthCode ? ['code'] : []
    }
    for (const t of raw) {
      if (t !== 'code') {
        throw new BadRequestError(`unsupported response_type: ${t}`)
      }
    }
    if (!hasAuthCode) {
      throw new BadRequestError(
        'response_type code requires the authorization_code grant',
      )
    }
    return ['code']
  }

  private resolveScopes(scope?: string): string[] {
    const mcp = new Set(this.appConfig.mcpScopes || [])
    const memberAllowed = new Set(
      this.scopeValidatorService.getAllowedScopeNamesForRole(false),
    )
    // With no scope asked for, the agent preset is offered as far as this instance's MCP_SCOPES allows (a scope an operator has not
    // enabled, such as code:write, is left out); a scope that is asked for by name and not allowed is still refused.
    const requested = scope
      ? this.scopeValidatorService.parseScopes(scope)
      : AgentMcpScopes.filter((s) => mcp.size === 0 || mcp.has(s))

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
    const orgId = await getIdentityPort().oldestOrgId()
    if (!orgId) {
      throw new BadRequestError('Instance is not initialized')
    }
    // No session here: the server itself reads the install's organisation.
    const oldestUserId = await forOrg(orgId, 'oauth.dcr', () =>
      getUserRepository().getOldestId(orgId),
    )
    if (!oldestUserId) {
      throw new BadRequestError('Instance has no users')
    }
    this.logger.info('DCR bound to instance org', { orgId })
    return { orgId, createdBy: oldestUserId }
  }
}
