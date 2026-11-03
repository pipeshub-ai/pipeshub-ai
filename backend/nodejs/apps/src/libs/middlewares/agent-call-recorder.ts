import type { Response } from 'express';
import { getAgentCallRepository } from '../repositories/agent-call-event';
import type {
  AgentTokenKind,
  NewAgentCall,
} from '../repositories/agent-call-event';
import { makeOrgContext, runWithOrgContext } from '../context/orgContext';
import { clientTags } from '../services/telemetry/identity';
import { Logger } from '../services/logger.service';
import {
  PAT_APP_CLIENT_ID_PREFIX,
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
} from '../../modules/oauth_provider/constants/constants';
import type { AuthenticatedUserRequest } from './types';

const logger = Logger.getInstance({ service: 'AgentCallRecorder' });

const MCP_BASE = '/mcp';

const scopeDenied = new WeakSet();

/** Flags a request refused for lack of a scope, so its row says so. */
export function markScopeDenied(req: object): void {
  scopeDenied.add(req);
}

/** The tool name of a single MCP `tools/call` message, else undefined. Names only, never arguments. */
function mcpToolName(body: unknown): string | undefined {
  const m = body as
    { method?: unknown; params?: { name?: unknown } } | undefined;
  return m?.method === 'tools/call' && typeof m.params?.name === 'string'
    ? m.params.name
    : undefined;
}

function tokenKindOf(user: Record<string, unknown>): AgentTokenKind {
  const clientId =
    typeof user.oauthClientId === 'string' ? user.oauthClientId : '';
  if (clientId.startsWith(PAT_APP_CLIENT_ID_PREFIX)) return 'pat';
  if (
    user.delegatedBy !== undefined ||
    clientId.startsWith(SERVICE_TOKEN_APP_CLIENT_ID_PREFIX)
  ) {
    return 'delegated';
  }
  return 'oauth';
}

/**
 * Records one row for this request once its response has finished, when it was made with a bearer token that the auth
 * middleware has just resolved (`req.user.isOAuth`; browser sessions are not recorded). Call it right after authentication.
 *
 * It uses only what authentication already resolved, so it adds no read to the request. The write is not awaited and its
 * failure is one warning: the audit trail never decides whether a call succeeds.
 *
 * shortcut: one write per recorded call, no queue or batching, so a database outage costs a warning per call and a very
 * busy org costs one insert per non-GET call; batch the writes when sustained writes pass a few hundred per second.
 */
export function recordAgentCall(
  req: AuthenticatedUserRequest,
  res: Response,
): void {
  const user = req.user;
  const tokenId: unknown = user?.tokenId;
  if (!user?.isOAuth || typeof tokenId !== 'string') return;
  const orgId = String(user.orgId);
  const userId = String(user.userId);
  // The mount of the router that authenticated: by the time the response ends, an error has already unwound `baseUrl`.
  const base = req.baseUrl;
  const started = Date.now();

  res.on('finish', () => {
    try {
      const routePath: unknown = (req.route as { path?: unknown } | undefined)
        ?.path;
      const route =
        typeof routePath === 'string'
          ? (base + routePath).replace(/(.)\/$/, '$1')
          : '(unmatched)';
      const mcpTool = base === MCP_BASE ? mcpToolName(req.body) : undefined;
      const statusCode = res.statusCode;
      const kind = tokenKindOf(user);
      const { user_agent, channel } = clientTags(req.headers);
      const requestId = (req as { context?: { requestId?: string } }).context
        ?.requestId;
      const call: NewAgentCall = {
        orgId,
        userId,
        tokenId,
        tokenKind: kind,
        ...(kind === 'oauth' &&
          typeof user.oauthClientId === 'string' && {
            oauthClientId: user.oauthClientId,
          }),
        ...(user_agent !== undefined && { userAgent: user_agent }),
        ...(channel !== undefined && { channel }),
        method: req.method,
        route,
        ...(mcpTool !== undefined && { mcpTool: mcpTool.slice(0, 200) }),
        statusCode,
        durationMs: Date.now() - started,
        scopeDenied: scopeDenied.has(req),
        ...(requestId !== undefined && { requestId }),
        rollup:
          req.method === 'GET' && statusCode < 400 && mcpTool === undefined,
      };
      void runWithOrgContext(
        makeOrgContext({ orgId, userId, kind: 'user', source: 'agent_audit' }),
        () => getAgentCallRepository().record(call),
      ).catch(warn);
    } catch (err) {
      warn(err);
    }
  });
}

function warn(err: unknown): void {
  logger.warn('Could not record an agent call', {
    error: err instanceof Error ? err.message : 'Unknown error',
  });
}
