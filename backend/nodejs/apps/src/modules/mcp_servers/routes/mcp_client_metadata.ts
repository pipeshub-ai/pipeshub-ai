import { NextFunction, Request, Response } from 'express';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { configPaths } from '../../configuration_manager/paths/paths';

/**
 * PipesHub's OAuth Client ID Metadata Document: MCP servers' authorization servers that support
 * it fetch this URL, PipesHub's client id, instead of PipesHub registering a client with them.
 * The Python side uses it only after fetching it here itself (`app/agents/mcp/cimd.py`).
 */
export const MCP_CLIENT_METADATA_PATH =
  '/mcp-servers/oauth/client-metadata.json';
const MCP_OAUTH_CALLBACK_PATH = '/mcp-servers/oauth/callback/';

interface StoredEndpoints {
  frontend?: { publicEndpoint?: unknown };
}

/**
 * Built from the configured public address the way the Python side builds the client id and
 * the redirect URI (`cimd.client_metadata_url`, `mcp_servers._mcp_oauth_redirect_uri`): trimmed,
 * every trailing slash removed. Any difference and the authorization server refuses the client.
 */
export function mcpClientMetadataDocument(
  frontendUrl: string,
): Record<string, unknown> {
  const base = frontendUrl.trim().replace(/\/+$/, '');
  return {
    client_id: `${base}${MCP_CLIENT_METADATA_PATH}`,
    client_name: 'PipesHub',
    client_uri: base,
    redirect_uris: [`${base}${MCP_OAUTH_CALLBACK_PATH}`],
    grant_types: ['authorization_code', 'refresh_token'],
    response_types: ['code'],
    token_endpoint_auth_method: 'none',
  };
}

/** Public: the authorization server fetches it without credentials. The address is read on each request, so a changed one applies without a restart. */
export function createMcpClientMetadataHandler(
  keyValueStore: KeyValueStoreService,
) {
  return async (
    _req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const stored = await keyValueStore.get<string>(configPaths.endpoint);
      const endpoints =
        typeof stored === 'string' && stored !== ''
          ? (JSON.parse(stored) as StoredEndpoints | null)
          : null;
      const frontendUrl = endpoints?.frontend?.publicEndpoint;
      if (typeof frontendUrl !== 'string' || frontendUrl.trim() === '') {
        res.status(404).type('text/plain').send('Not Found');
        return;
      }
      res
        .set('Cache-Control', 'public, max-age=300')
        .json(mcpClientMetadataDocument(frontendUrl));
    } catch (error) {
      next(error);
    }
  };
}
