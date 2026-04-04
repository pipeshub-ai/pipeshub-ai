import crypto from 'crypto';
import axios, { AxiosError } from 'axios';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import {
  BadRequestError,
  NotFoundError,
  BadGatewayError,
  UnprocessableEntityError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import {
  ProtectedResourceMetadata,
  AuthServerMetadata,
  DcrResponse,
  OAuthFlowState,
  StoredTokenData,
  StartAuthRequest,
  StartAuthResponse,
  TokenResponse,
  McpConnectionInfo,
  ClientMetadataDocument,
  ClientRegistrationMethod,
  PkceChallenge,
  WwwAuthenticateParams,
  McpAuthType,
  ConnectBearerRequest,
} from '../types/mcp-client.types';

const FLOW_STATE_TTL_MS = 10 * 60 * 1000; // 10 minutes
const TOKEN_REFRESH_BUFFER_MS = 60 * 1000; // 60 seconds before expiry
const HTTP_TIMEOUT_MS = 10_000;

export class McpClientService {
  private logger: Logger;
  private kvStore: KeyValueStoreService;
  private encryptionService: EncryptionService;
  private appBaseUrl: string;
  private frontendBaseUrl: string;

  constructor(
    logger: Logger,
    kvStore: KeyValueStoreService,
    encryptionService: EncryptionService,
    appBaseUrl: string,
    frontendBaseUrl: string,
  ) {
    this.logger = logger;
    this.kvStore = kvStore;
    this.encryptionService = encryptionService;
    this.appBaseUrl = appBaseUrl;
    this.frontendBaseUrl = frontendBaseUrl;
  }

  // ============================================================================
  // Public Methods
  // ============================================================================

  async startAuth(
    orgId: string,
    userId: string,
    request: StartAuthRequest,
  ): Promise<StartAuthResponse> {
    const { mcpServerUrl, scope: requestedScope, clientId: preClientId, clientSecret: preClientSecret, frontendBaseUrl } = request;

    this.validateUrl(mcpServerUrl);

    // Step 1: Discovery
    const { resourceMetadata, wwwAuthScope } = await this.discoverProtectedResourceMetadata(mcpServerUrl);
    const authServerUrl = resourceMetadata.authorization_servers?.[0];
    if (!authServerUrl) {
      throw new BadGatewayError('No authorization server found in protected resource metadata');
    }

    const asMetadata = await this.discoverAuthServerMetadata(authServerUrl);

    // Step 2: Resolve client credentials — redirect to frontend SPA
    const redirectUri = `${frontendBaseUrl}/mcp/auth/callback`;
    const { clientId, clientSecret, registrationMethod } = await this.resolveClientCredentials(
      orgId,
      asMetadata,
      preClientId,
      preClientSecret,
      redirectUri,
    );

    // Step 3: PKCE + authorization URL
    const { codeVerifier, codeChallenge } = this.generatePkce();
    const state = crypto.randomBytes(32).toString('hex');

    // Scope selection priority: request → WWW-Authenticate → resource metadata
    const scope = requestedScope || wwwAuthScope || resourceMetadata.scopes_supported?.join(' ') || undefined;

    const authUrl = new URL(asMetadata.authorization_endpoint);
    authUrl.searchParams.set('response_type', 'code');
    authUrl.searchParams.set('client_id', clientId);
    authUrl.searchParams.set('redirect_uri', redirectUri);
    authUrl.searchParams.set('code_challenge', codeChallenge);
    authUrl.searchParams.set('code_challenge_method', 'S256');
    authUrl.searchParams.set('state', state);
    authUrl.searchParams.set('resource', mcpServerUrl);
    if (scope) {
      authUrl.searchParams.set('scope', scope);
    }

    // Store flow state
    const flowState: OAuthFlowState = {
      mcpServerUrl,
      codeVerifier,
      redirectUri,
      scope,
      orgId,
      userId,
      clientId,
      clientSecret,
      registrationMethod,
      tokenEndpoint: asMetadata.token_endpoint,
      resource: mcpServerUrl,
      createdAt: Date.now(),
    };

    const encryptedState = this.encryptionService.encrypt(JSON.stringify(flowState));
    await this.kvStore.set(`/mcp/auth/state/${state}`, encryptedState);

    this.logger.info('OAuth flow started', { mcpServerUrl, registrationMethod, orgId, userId });

    return {
      authorizationUrl: authUrl.toString(),
      state,
    };
  }

  async handleCallback(
    code: string,
    state: string,
  ): Promise<{ success: boolean; mcpServerUrl: string }> {
    // Look up flow state
    const encryptedState = await this.kvStore.get<string>(`/mcp/auth/state/${state}`);
    if (!encryptedState) {
      throw new NotFoundError('OAuth flow state not found or expired');
    }

    let flowState: OAuthFlowState;
    try {
      flowState = JSON.parse(this.encryptionService.decrypt(encryptedState));
    } catch {
      throw new BadRequestError('Invalid OAuth flow state');
    }

    // Manual TTL check
    if (Date.now() - flowState.createdAt > FLOW_STATE_TTL_MS) {
      await this.kvStore.delete(`/mcp/auth/state/${state}`);
      throw new BadRequestError('OAuth flow state expired');
    }

    const { orgId, userId, mcpServerUrl } = flowState;

    // Exchange code for tokens
    const tokenParams = new URLSearchParams();
    tokenParams.set('grant_type', 'authorization_code');
    tokenParams.set('code', code);
    tokenParams.set('redirect_uri', flowState.redirectUri);
    tokenParams.set('client_id', flowState.clientId);
    if (flowState.clientSecret) {
      tokenParams.set('client_secret', flowState.clientSecret);
    }
    tokenParams.set('code_verifier', flowState.codeVerifier);
    tokenParams.set('resource', flowState.resource);

    const tokenResponse = await this.httpPost(flowState.tokenEndpoint, tokenParams.toString(), {
      'Content-Type': 'application/x-www-form-urlencoded',
    });

    const { access_token, refresh_token, token_type, scope, expires_in } = tokenResponse;

    if (!access_token) {
      throw new BadGatewayError('Token endpoint did not return access_token');
    }

    // Encrypt sensitive fields individually
    const storedToken: StoredTokenData = {
      authType: McpAuthType.OAUTH,
      accessToken: this.encryptionService.encrypt(access_token),
      refreshToken: refresh_token ? this.encryptionService.encrypt(refresh_token) : undefined,
      tokenType: token_type || 'Bearer',
      scope: scope || flowState.scope,
      expiresAt: expires_in ? Date.now() + expires_in * 1000 : undefined,
      mcpServerUrl,
      clientId: flowState.clientId,
      clientSecret: flowState.clientSecret ? this.encryptionService.encrypt(flowState.clientSecret) : undefined,
      tokenEndpoint: flowState.tokenEndpoint,
      resource: flowState.resource,
      registrationMethod: flowState.registrationMethod,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    const serverUrlHash = this.hashUrl(mcpServerUrl);
    await this.kvStore.set(
      `/orgs/${orgId}/users/${userId}/mcp/${serverUrlHash}/token`,
      JSON.stringify(storedToken),
    );

    // Clean up flow state
    await this.kvStore.delete(`/mcp/auth/state/${state}`);

    this.logger.info('OAuth token exchange successful', { mcpServerUrl, orgId, userId });

    return { success: true, mcpServerUrl };
  }

  async getToken(
    orgId: string,
    userId: string,
    mcpServerUrl: string,
  ): Promise<TokenResponse> {
    const serverUrlHash = this.hashUrl(mcpServerUrl);
    const key = `/orgs/${orgId}/users/${userId}/mcp/${serverUrlHash}/token`;
    const stored = await this.kvStore.get<string>(key);

    if (!stored) {
      throw new NotFoundError('No stored token for this MCP server');
    }

    let storedToken: StoredTokenData;
    try {
      storedToken = JSON.parse(stored);
    } catch {
      throw new InternalServerError('Failed to parse stored token data');
    }

    const authType = storedToken.authType || McpAuthType.OAUTH;

    // Bearer tokens don't expire or need refresh
    if (authType === McpAuthType.OAUTH) {
      // Check if token needs refresh
      if (storedToken.expiresAt && storedToken.expiresAt - Date.now() < TOKEN_REFRESH_BUFFER_MS) {
        if (storedToken.refreshToken) {
          try {
            storedToken = await this.refreshAccessToken(orgId, userId, storedToken);
          } catch (error: any) {
            this.logger.warn('Token refresh failed, removing stale token', {
              error: error.message,
              mcpServerUrl,
            });
            await this.kvStore.delete(key);
            throw new NotFoundError('Token expired and refresh failed');
          }
        } else {
          // No refresh token, remove stale token
          await this.kvStore.delete(key);
          throw new NotFoundError('Token expired and no refresh token available');
        }
      }
    }

    let accessToken: string;
    try {
      accessToken = this.encryptionService.decrypt(storedToken.accessToken);
    } catch {
      throw new InternalServerError('Failed to decrypt access token');
    }

    return {
      accessToken,
      tokenType: storedToken.tokenType,
      expiresAt: storedToken.expiresAt,
      scope: storedToken.scope,
      mcpServerUrl: storedToken.mcpServerUrl,
      authType,
    };
  }

  async deleteToken(orgId: string, userId: string, mcpServerUrl: string): Promise<void> {
    const serverUrlHash = this.hashUrl(mcpServerUrl);
    const key = `/orgs/${orgId}/users/${userId}/mcp/${serverUrlHash}/token`;
    await this.kvStore.delete(key);
    this.logger.info('Token deleted', { mcpServerUrl, orgId, userId });
  }

  async listConnections(orgId: string, userId: string): Promise<McpConnectionInfo[]> {
    const prefix = `/orgs/${orgId}/users/${userId}/mcp/`;
    const keys = await this.kvStore.listKeysInDirectory(prefix);

    const connections: McpConnectionInfo[] = [];
    for (const key of keys) {
      if (!key.endsWith('/token')) continue;
      try {
        const stored = await this.kvStore.get<string>(key);
        if (!stored) continue;
        const storedToken: StoredTokenData = JSON.parse(stored);
        connections.push({
          mcpServerUrl: storedToken.mcpServerUrl,
          authType: storedToken.authType || McpAuthType.OAUTH,
          scope: storedToken.scope,
          expiresAt: storedToken.expiresAt,
          createdAt: storedToken.createdAt,
          registrationMethod: storedToken.registrationMethod,
        });
      } catch {
        this.logger.warn('Failed to parse stored token', { key });
      }
    }

    return connections;
  }

  getClientMetadataDocument(): ClientMetadataDocument {
    const redirectUri = `${this.frontendBaseUrl}/mcp/auth/callback`;
    return {
      client_id: `${this.appBaseUrl}/api/v1/mcp/client-metadata.json`,
      client_name: 'PipesHub MCP Client',
      redirect_uris: [redirectUri],
      grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'],
      token_endpoint_auth_method: 'none',
    };
  }

  async connectBearer(
    orgId: string,
    userId: string,
    request: ConnectBearerRequest,
  ): Promise<{ success: boolean; mcpServerUrl: string }> {
    const { mcpServerUrl, bearerToken } = request;

    this.validateUrl(mcpServerUrl);
    await this.validateBearerToken(mcpServerUrl, bearerToken);

    const storedToken: StoredTokenData = {
      authType: McpAuthType.BEARER_TOKEN,
      accessToken: this.encryptionService.encrypt(bearerToken),
      tokenType: 'Bearer',
      mcpServerUrl,
      registrationMethod: ClientRegistrationMethod.BEARER_TOKEN,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    const serverUrlHash = this.hashUrl(mcpServerUrl);
    await this.kvStore.set(
      `/orgs/${orgId}/users/${userId}/mcp/${serverUrlHash}/token`,
      JSON.stringify(storedToken),
    );

    this.logger.info('Bearer token connection stored', { mcpServerUrl, orgId, userId });

    return { success: true, mcpServerUrl };
  }

  // ============================================================================
  // Private Helpers — Discovery
  // ============================================================================

  private async discoverProtectedResourceMetadata(
    mcpServerUrl: string,
  ): Promise<{ resourceMetadata: ProtectedResourceMetadata; wwwAuthScope?: string }> {
    let wwwAuthScope: string | undefined;
    let resourceMetadataUrl: string | undefined;

    // Step 1: Hit the MCP server unauthenticated → expect 401 with WWW-Authenticate
    try {
      const response = await axios.get(mcpServerUrl, {
        timeout: HTTP_TIMEOUT_MS,
        validateStatus: (status) => status === 401 || status === 200,
      });

      if (response.status === 401) {
        const wwwAuth = response.headers['www-authenticate'];
        if (wwwAuth) {
          const params = this.parseWwwAuthenticate(wwwAuth);
          resourceMetadataUrl = params.resource_metadata;
          wwwAuthScope = params.scope;
        }
      }
    } catch {
      // Fall through to well-known fallback
    }

    // If resource_metadata URL found, fetch it
    if (resourceMetadataUrl) {
      try {
        const response = await axios.get(resourceMetadataUrl, { timeout: HTTP_TIMEOUT_MS });
        return { resourceMetadata: response.data, wwwAuthScope };
      } catch {
        this.logger.warn('Failed to fetch resource metadata from WWW-Authenticate URL', { resourceMetadataUrl });
      }
    }

    // Fallback: well-known URLs
    const parsed = new URL(mcpServerUrl);
    const origin = parsed.origin;
    const pathname = parsed.pathname === '/' ? '' : parsed.pathname;

    const wellKnownUrls: string[] = [];
    if (pathname) {
      wellKnownUrls.push(`${origin}/.well-known/oauth-protected-resource${pathname}`);
    }
    wellKnownUrls.push(`${origin}/.well-known/oauth-protected-resource`);

    for (const url of wellKnownUrls) {
      try {
        const response = await axios.get(url, { timeout: HTTP_TIMEOUT_MS });
        if (response.data && response.data.resource) {
          return { resourceMetadata: response.data, wwwAuthScope };
        }
      } catch {
        continue;
      }
    }

    throw new BadGatewayError(
      `Failed to discover protected resource metadata for ${mcpServerUrl}`,
    );
  }

  private async discoverAuthServerMetadata(authServerUrl: string): Promise<AuthServerMetadata> {
    const parsed = new URL(authServerUrl);
    const origin = parsed.origin;
    const pathname = parsed.pathname === '/' ? '' : parsed.pathname;

    const wellKnownUrls: string[] = [];
    if (pathname) {
      wellKnownUrls.push(`${origin}/.well-known/oauth-authorization-server${pathname}`);
      wellKnownUrls.push(`${origin}/.well-known/openid-configuration${pathname}`);
      wellKnownUrls.push(`${authServerUrl}/.well-known/openid-configuration`);
    } else {
      wellKnownUrls.push(`${origin}/.well-known/oauth-authorization-server`);
      wellKnownUrls.push(`${origin}/.well-known/openid-configuration`);
    }

    for (const url of wellKnownUrls) {
      try {
        const response = await axios.get(url, { timeout: HTTP_TIMEOUT_MS });
        const metadata: AuthServerMetadata = response.data;

        if (metadata.authorization_endpoint && metadata.token_endpoint) {
          // Validate S256 support
          if (
            metadata.code_challenge_methods_supported &&
            !metadata.code_challenge_methods_supported.includes('S256')
          ) {
            throw new BadGatewayError(
              'Authorization server does not support S256 code challenge method',
            );
          }
          return metadata;
        }
      } catch (error: any) {
        if (error instanceof BadGatewayError) throw error;
        continue;
      }
    }

    throw new BadGatewayError(
      `Failed to discover authorization server metadata for ${authServerUrl}`,
    );
  }

  // ============================================================================
  // Private Helpers — Client Registration
  // ============================================================================

  private async resolveClientCredentials(
    orgId: string,
    asMetadata: AuthServerMetadata,
    preClientId?: string,
    preClientSecret?: string,
    redirectUri?: string,
  ): Promise<{ clientId: string; clientSecret?: string; registrationMethod: ClientRegistrationMethod }> {
    // 1. Pre-registered
    if (preClientId) {
      return {
        clientId: preClientId,
        clientSecret: preClientSecret,
        registrationMethod: ClientRegistrationMethod.PRE_REGISTERED,
      };
    }

    // 2. Client ID Metadata Document
    if (asMetadata.client_id_metadata_document_supported) {
      return {
        clientId: `${this.appBaseUrl}/api/v1/mcp/client-metadata.json`,
        registrationMethod: ClientRegistrationMethod.CLIENT_ID_METADATA_DOCUMENT,
      };
    }

    // 3. DCR
    if (asMetadata.registration_endpoint) {
      const result = await this.registerClient(orgId, asMetadata.registration_endpoint, redirectUri!);
      return {
        clientId: result.client_id,
        clientSecret: result.client_secret,
        registrationMethod: ClientRegistrationMethod.DCR,
      };
    }

    // 4. None available
    throw new UnprocessableEntityError(
      'No client registration method available. Please provide clientId and clientSecret.',
    );
  }

  private async registerClient(
    orgId: string,
    registrationEndpoint: string,
    redirectUri: string,
  ): Promise<DcrResponse> {
    const authServerHash = this.hashUrl(registrationEndpoint);
    const redirectHash = this.hashUrl(redirectUri);
    const cacheKey = `/orgs/${orgId}/mcp/dcr/${authServerHash}/${redirectHash}`;

    // Check DCR cache first
    const cached = await this.kvStore.get<string>(cacheKey);
    if (cached) {
      try {
        const dcrResponse: DcrResponse & { client_secret_encrypted?: string } = JSON.parse(cached);
        // Decrypt cached client_secret if present
        if (dcrResponse.client_secret_encrypted) {
          dcrResponse.client_secret = this.encryptionService.decrypt(dcrResponse.client_secret_encrypted);
          delete dcrResponse.client_secret_encrypted;
        }
        // Check if expired
        if (!dcrResponse.client_secret_expires_at || dcrResponse.client_secret_expires_at * 1000 > Date.now()) {
          return dcrResponse;
        }
      } catch {
        this.logger.warn('Failed to parse cached DCR response', { cacheKey });
      }
    }

    // Register new client
    const body = {
      client_name: 'PipesHub MCP Client',
      redirect_uris: [redirectUri],
      grant_types: ['authorization_code', 'refresh_token'],
      response_types: ['code'],
      token_endpoint_auth_method: 'client_secret_post',
    };

    const response = await this.httpPost(registrationEndpoint, JSON.stringify(body), {
      'Content-Type': 'application/json',
    });

    const dcrResponse: DcrResponse = response;

    // Cache DCR result (encrypt client_secret)
    const toCache: any = { ...dcrResponse };
    if (toCache.client_secret) {
      toCache.client_secret_encrypted = this.encryptionService.encrypt(toCache.client_secret);
      delete toCache.client_secret;
    }
    await this.kvStore.set(cacheKey, JSON.stringify(toCache));

    this.logger.info('DCR registration successful', { orgId, clientId: dcrResponse.client_id });
    return dcrResponse;
  }

  // ============================================================================
  // Private Helpers — PKCE
  // ============================================================================

  private generatePkce(): PkceChallenge {
    const codeVerifier = crypto.randomBytes(32).toString('base64url');
    const codeChallenge = crypto
      .createHash('sha256')
      .update(codeVerifier)
      .digest('base64url');
    return { codeVerifier, codeChallenge };
  }

  // ============================================================================
  // Private Helpers — Token Refresh
  // ============================================================================

  private async refreshAccessToken(
    orgId: string,
    userId: string,
    storedToken: StoredTokenData,
  ): Promise<StoredTokenData> {
    if ((storedToken.authType || McpAuthType.OAUTH) === McpAuthType.BEARER_TOKEN) {
      throw new Error('Bearer token connections do not support refresh');
    }

    if (!storedToken.refreshToken) {
      throw new Error('No refresh token available');
    }

    if (!storedToken.clientId || !storedToken.tokenEndpoint || !storedToken.resource) {
      throw new Error('Missing OAuth fields required for token refresh');
    }

    const refreshToken = this.encryptionService.decrypt(storedToken.refreshToken);
    const clientSecret = storedToken.clientSecret
      ? this.encryptionService.decrypt(storedToken.clientSecret)
      : undefined;

    const params = new URLSearchParams();
    params.set('grant_type', 'refresh_token');
    params.set('refresh_token', refreshToken);
    params.set('client_id', storedToken.clientId);
    if (clientSecret) {
      params.set('client_secret', clientSecret);
    }
    params.set('resource', storedToken.resource);

    const response = await this.httpPost(storedToken.tokenEndpoint, params.toString(), {
      'Content-Type': 'application/x-www-form-urlencoded',
    });

    const { access_token, refresh_token: newRefreshToken, expires_in, scope } = response;

    if (!access_token) {
      throw new Error('Refresh token response did not return access_token');
    }

    const updatedToken: StoredTokenData = {
      ...storedToken,
      accessToken: this.encryptionService.encrypt(access_token),
      refreshToken: newRefreshToken
        ? this.encryptionService.encrypt(newRefreshToken)
        : storedToken.refreshToken,
      expiresAt: expires_in ? Date.now() + expires_in * 1000 : storedToken.expiresAt,
      scope: scope || storedToken.scope,
      updatedAt: Date.now(),
    };

    const serverUrlHash = this.hashUrl(storedToken.mcpServerUrl);
    await this.kvStore.set(
      `/orgs/${orgId}/users/${userId}/mcp/${serverUrlHash}/token`,
      JSON.stringify(updatedToken),
    );

    this.logger.info('Token refreshed successfully', {
      mcpServerUrl: storedToken.mcpServerUrl,
      orgId,
      userId,
    });

    return updatedToken;
  }

  // ============================================================================
  // Private Helpers — Bearer Token Validation
  // ============================================================================

  private async validateBearerToken(mcpServerUrl: string, bearerToken: string): Promise<void> {
    try {
      const response = await axios.get(mcpServerUrl, {
        headers: { Authorization: `Bearer ${bearerToken}` },
        timeout: HTTP_TIMEOUT_MS,
        validateStatus: () => true,
      });

      if (response.status === 401 || response.status === 403) {
        throw new BadRequestError('Bearer token was rejected by the MCP server');
      }
    } catch (error: any) {
      if (error instanceof BadRequestError) throw error;
      if (error instanceof AxiosError) {
        throw new BadGatewayError(`Cannot reach MCP server at ${mcpServerUrl}: ${error.message}`);
      }
      throw error;
    }
  }

  // ============================================================================
  // Private Helpers — Utilities
  // ============================================================================

  private hashUrl(url: string): string {
    return crypto.createHash('sha256').update(url).digest('hex').substring(0, 16);
  }

  private parseWwwAuthenticate(header: string): WwwAuthenticateParams {
    const params: WwwAuthenticateParams = {};
    // Remove "Bearer " prefix if present
    const value = header.replace(/^Bearer\s+/i, '');

    // Match key="value" or key=value patterns
    const regex = /(\w+)=(?:"([^"]*)"|([\w.:/\-~]+))/g;
    let match: RegExpExecArray | null;
    while ((match = regex.exec(value)) !== null) {
      const key = match[1] as keyof WwwAuthenticateParams;
      const val = match[2] ?? match[3];
      (params as any)[key] = val;
    }
    return params;
  }

  private validateUrl(url: string): void {
    try {
      const parsed = new URL(url);
      // Allow HTTP for localhost, require HTTPS otherwise
      if (parsed.protocol !== 'https:' && parsed.hostname !== 'localhost' && parsed.hostname !== '127.0.0.1') {
        throw new BadRequestError('MCP server URL must use HTTPS (except for localhost)');
      }
    } catch (error: any) {
      if (error instanceof BadRequestError) throw error;
      throw new BadRequestError(`Invalid URL: ${url}`);
    }
  }

  private async httpPost(url: string, body: string, headers: Record<string, string>): Promise<any> {
    try {
      const response = await axios.post(url, body, {
        headers,
        timeout: HTTP_TIMEOUT_MS,
      });
      return response.data;
    } catch (error: any) {
      if (error instanceof AxiosError) {
        const status = error.response?.status;
        const data = error.response?.data;
        const message = typeof data === 'object' && data?.error_description
          ? data.error_description
          : typeof data === 'object' && data?.error
            ? data.error
            : error.message;
        throw new BadGatewayError(`HTTP POST to ${url} failed (${status}): ${message}`);
      }
      throw error;
    }
  }
}
