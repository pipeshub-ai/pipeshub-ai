/**
 * MCP Servers Controller
 *
 * Thin proxy layer in front of the Python connectors service's
 * `/api/v1/mcp-servers` router (`backend/python/app/api/routes/mcp_servers.py`).
 * Every route here is a 1:1 forward: catalog, org-scoped instance CRUD,
 * non-OAuth auth (API token/headers), OAuth+DCR authorize/callback/refresh,
 * and tool discovery.
 *
 * Unlike `connector.controllers.ts`, this module does NOT compute or forward
 * an `X-Is-Admin` header — the Python side independently verifies admin
 * status by calling back into Node's own `/api/v1/users/{userId}/adminCheck`
 * using the forwarded `Authorization` header (mirrors `toolsets.py`'s
 * `_check_user_is_admin`). So the only job here is to forward headers, body,
 * and query params, and translate the upstream response/error — same shape
 * as `toolsets_controller.ts`, collapsed into one factory since every MCP
 * route is a plain forward with no response post-processing.
 */

import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { UnauthorizedError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { redactSensitiveQueryParams } from '../../../libs/utils/log-redaction.utils';
import {
  executeConnectorCommand,
  handleBackendError,
  handleConnectorResponse,
} from '../../tokens_manager/utils/connector.utils';
import { revealQuery } from '../../configuration_manager/utils/secretReveal';

const logger = Logger.getInstance({
  service: 'McpServersController',
});

const MCP_BASE = '/api/v1/mcp-servers';

type HttpMethodValue = (typeof HttpMethod)[keyof typeof HttpMethod];
type PathBuilder = (req: AuthenticatedUserRequest) => string;

const BODY_METHODS = new Set<HttpMethodValue>([
  HttpMethod.POST,
  HttpMethod.PUT,
  HttpMethod.PATCH,
]);

// Headers we forward to the Python connectors backend. Authorization carries the
// verified caller identity; tracing headers preserve request correlation. Everything
// else (cookie, host, user-agent, arbitrary client-supplied x-* headers) is dropped —
// mirrors `connector.controllers.ts`'s `buildProxyHeaders` allowlist.
const PROXY_FORWARD_HEADERS: readonly string[] = [
  'authorization',
  'x-request-id',
  'x-correlation-id',
  'accept-language',
];

function buildProxyHeaders(req: AuthenticatedUserRequest): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const name of PROXY_FORWARD_HEADERS) {
    const value = req.headers[name];
    if (typeof value === 'string') {
      headers[name] = value;
    } else if (Array.isArray(value)) {
      headers[name] = value.join(',');
    }
  }
  return headers;
}

function encInstanceId(req: AuthenticatedUserRequest): string {
  return encodeURIComponent(String(req.params.instanceId));
}

function encTypeId(req: AuthenticatedUserRequest): string {
  return encodeURIComponent(String(req.params.typeId));
}

/** Builds `?k=v&...` from selected query keys, skipping undefined/empty values. */
function queryString(query: Record<string, unknown>, keys: string[]): string {
  const params = new URLSearchParams();
  for (const key of keys) {
    const value = query[key];
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

/** `queryString`, plus `reveal=true` when the caller may ask for stored values (`revealQuery`). */
function queryWithReveal(req: AuthenticatedUserRequest, keys: string[]): string {
  const qs = queryString(req.query as Record<string, unknown>, keys);
  const reveal = revealQuery(req).slice(1);
  if (!reveal) return qs;
  return qs ? `${qs}&${reveal}` : `?${reveal}`;
}

/**
 * Factory for the common case: forward method + path + (optional) body to
 * the Python connectors backend, return its response verbatim.
 */
// Above the longest Python-side MCP work, so Python's own error arrives before this fires.
// The longest is one server's tool discovery after an expired token: a refresh (15 s token
// request) then a connect (at most 45 s, `connectTimeoutSeconds`) plus listing (15 s) — 75 s.
// Listings that cover several servers cap each one at 10 s.
export const MCP_PROXY_TIMEOUT_MS = 90_000;

/** `sendsOnce`: a GET that changes state (an OAuth state claimed or created) — never resent. */
function proxyMcp(
  method: HttpMethodValue,
  pathBuilder: PathBuilder,
  action: string,
  { sendsOnce = method !== HttpMethod.GET }: { sendsOnce?: boolean } = {},
) {
  return (appConfig: AppConfig) =>
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        const { userId } = req.user || {};
        if (!userId) {
          throw new UnauthorizedError('User authentication required');
        }

        // The OAuth callback's path carries the authorization code and state.
        logger.debug(`MCP servers proxy: ${action}`, {
          userId,
          path: redactSensitiveQueryParams(pathBuilder(req)),
        });

        const headers = buildProxyHeaders(req);

        const connectorResponse = await executeConnectorCommand(
          `${appConfig.connectorBackend}${MCP_BASE}${pathBuilder(req)}`,
          method,
          headers,
          BODY_METHODS.has(method) ? req.body : undefined,
          // A resent write could create a second instance or start a second authorization.
          { timeoutMs: MCP_PROXY_TIMEOUT_MS, retries: sendsOnce ? 1 : 3 },
        );

        handleConnectorResponse(connectorResponse, res, action, `${action} failed`);
      } catch (error: any) {
        logger.error(`Error in MCP servers proxy: ${action}`, {
          error: error.message,
          userId: req.user?.userId,
          status: error.response?.status || error.statusCode,
          data: error.response?.data || error.data,
        });
        next(handleBackendError(error, action));
      }
    };
}

// ============================================================================
// Catalog (read-only, in-memory templates)
// ============================================================================

export const listMcpCatalog = proxyMcp(
  HttpMethod.GET,
  (req) => `/catalog${queryString(req.query as Record<string, unknown>, ['page', 'limit', 'search'])}`,
  'List MCP catalog',
);

export const getMcpCatalogTemplate = proxyMcp(
  HttpMethod.GET,
  (req) => `/catalog/${encTypeId(req)}`,
  'Get MCP catalog template',
);

// ============================================================================
// Instances — an admin's (org-wide) or a user's own (personal)
// ============================================================================

export const listMcpInstances = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances${queryWithReveal(req, ['includePersonal'])}`,
  'List MCP server instances',
);

export const createMcpInstance = proxyMcp(HttpMethod.POST, () => '/instances', 'Create MCP server instance');

export const getMcpInstance = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances/${encInstanceId(req)}`,
  'Get MCP server instance',
);

export const updateMcpInstance = proxyMcp(
  HttpMethod.PUT,
  (req) => `/instances/${encInstanceId(req)}`,
  'Update MCP server instance',
);

export const deleteMcpInstance = proxyMcp(
  HttpMethod.DELETE,
  (req) => `/instances/${encInstanceId(req)}`,
  'Delete MCP server instance',
);

// ============================================================================
// Auth — API token / headers
// ============================================================================

export const authenticateMcpInstance = proxyMcp(
  HttpMethod.POST,
  (req) => `/instances/${encInstanceId(req)}/authenticate`,
  'Authenticate MCP server instance',
);

export const updateMcpCredentials = proxyMcp(
  HttpMethod.PUT,
  (req) => `/instances/${encInstanceId(req)}/credentials`,
  'Update MCP server credentials',
);

export const removeMcpCredentials = proxyMcp(
  HttpMethod.DELETE,
  (req) => `/instances/${encInstanceId(req)}/credentials`,
  'Remove MCP server credentials',
);

export const autoAuthenticateMcpInstance = proxyMcp(
  HttpMethod.POST,
  (req) => `/instances/${encInstanceId(req)}/auto-authenticate`,
  'Auto-authenticate MCP server instance',
);

export const reauthenticateMcpInstance = proxyMcp(
  HttpMethod.POST,
  (req) => `/instances/${encInstanceId(req)}/reauthenticate`,
  'Re-authenticate MCP server instance',
);

// ============================================================================
// OAuth + DCR
// ============================================================================

export const getMcpOAuthAuthorizationUrl = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances/${encInstanceId(req)}/oauth/authorize${queryString(req.query as Record<string, unknown>, ['baseUrl'])}`,
  'Get MCP OAuth authorization URL',
  { sendsOnce: true },
);

export const handleMcpOAuthCallback = proxyMcp(
  HttpMethod.GET,
  (req) => `/oauth/callback${queryString(req.query as Record<string, unknown>, ['code', 'state', 'error'])}`,
  'Handle MCP OAuth callback',
  { sendsOnce: true },
);

export const refreshMcpOAuthToken = proxyMcp(
  HttpMethod.POST,
  (req) => `/instances/${encInstanceId(req)}/oauth/refresh`,
  'Refresh MCP OAuth token',
);

export const getMcpOAuthConfig = proxyMcp(
  HttpMethod.GET,
  (req) =>
    `/instances/${encInstanceId(req)}/oauth-config${revealQuery(req)}`,
  'Get MCP OAuth client configuration',
);

export const updateMcpOAuthConfig = proxyMcp(
  HttpMethod.PUT,
  (req) => `/instances/${encInstanceId(req)}/oauth-config`,
  'Update MCP OAuth client configuration',
);

export const discoverMcpOAuthMetadata = proxyMcp(
  HttpMethod.POST,
  () => '/oauth/discover',
  'Discover MCP OAuth metadata',
);

// ============================================================================
// Discovery / consumption
// ============================================================================

export const getMyMcpServers = proxyMcp(
  HttpMethod.GET,
  (req) => `/my-mcp-servers${queryString(req.query as Record<string, unknown>, ['includeTools'])}`,
  'Get my MCP servers',
);

export const getMcpInstanceTools = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances/${encInstanceId(req)}/tools${queryString(req.query as Record<string, unknown>, ['cached'])}`,
  'Get MCP server instance tools',
);

// ============================================================================
// Agent-scoped (service-account agent) MCP credential management
//
// Mirrors `toolsets_controller.ts`'s `getAgentToolsets`/`authenticateAgentToolset`/etc.
// — one-to-one forwards to Python's `/agents/{agentKey}/...` routes
// (`mcp_servers.py`), which independently re-checks agent edit access
// (`_require_mcp_agent_edit_access`) regardless of the scope gate applied at
// the route layer here.
// ============================================================================

function encAgentKey(req: AuthenticatedUserRequest): string {
  return encodeURIComponent(String(req.params.agentKey));
}

// Tool approvals: company rules (admins), a person's own rules, an agent's rules (its editors).
// Python checks who may read and change each.

export const getMcpToolPolicy = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances/${encInstanceId(req)}/tool-policy`,
  'Get MCP server company tool rules',
);

export const updateMcpToolPolicy = proxyMcp(
  HttpMethod.PUT,
  (req) => `/instances/${encInstanceId(req)}/tool-policy`,
  'Update MCP server company tool rules',
);

export const getMyMcpToolRules = proxyMcp(
  HttpMethod.GET,
  (req) => `/instances/${encInstanceId(req)}/my-tool-rules`,
  'Get my MCP tool rules',
);

export const updateMyMcpToolRules = proxyMcp(
  HttpMethod.PUT,
  (req) => `/instances/${encInstanceId(req)}/my-tool-rules`,
  'Update my MCP tool rules',
);

export const getAgentMcpToolRules = proxyMcp(
  HttpMethod.GET,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/tool-rules`,
  'Get agent MCP tool rules',
);

export const updateAgentMcpToolRules = proxyMcp(
  HttpMethod.PUT,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/tool-rules`,
  'Update agent MCP tool rules',
);

export const getAgentMcpServers = proxyMcp(
  HttpMethod.GET,
  (req) =>
    `/agents/${encAgentKey(req)}${queryString(req.query as Record<string, unknown>, ['includeTools'])}`,
  'Get agent MCP servers',
);

export const authenticateAgentMcpInstance = proxyMcp(
  HttpMethod.POST,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/authenticate`,
  'Authenticate agent MCP server instance',
);

export const updateAgentMcpCredentials = proxyMcp(
  HttpMethod.PUT,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/credentials`,
  'Update agent MCP server credentials',
);

export const removeAgentMcpCredentials = proxyMcp(
  HttpMethod.DELETE,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/credentials`,
  'Remove agent MCP server credentials',
);

export const reauthenticateAgentMcpInstance = proxyMcp(
  HttpMethod.POST,
  (req) => `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/reauthenticate`,
  'Re-authenticate agent MCP server instance',
);

export const getAgentMcpOAuthAuthorizationUrl = proxyMcp(
  HttpMethod.GET,
  (req) =>
    `/agents/${encAgentKey(req)}/instances/${encInstanceId(req)}/oauth/authorize${queryString(req.query as Record<string, unknown>, ['baseUrl'])}`,
  'Get agent MCP OAuth authorization URL',
  { sendsOnce: true },
);
