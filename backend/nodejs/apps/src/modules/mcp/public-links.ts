/**
 * The MCP package builds the links it hands to the user (connect a source,
 * open a collection) from its API client's base URL. The tools call the REST
 * API over loopback, an origin the user's browser may not reach, so the client
 * is given the public dashboard origin and each request is sent back to
 * loopback before it leaves.
 */
export interface McpLinkRouting {
  /** Base URL for the package's API client; its origin starts every link. */
  serverURL: string;
  /** Points a request built on `serverURL` back at the loopback API. */
  toLoopback: (request: Request) => Request;
}

export function mcpLinkRouting(
  loopbackServerURL: string,
  frontendUrl: string,
): McpLinkRouting {
  const unchanged: McpLinkRouting = {
    serverURL: loopbackServerURL,
    toLoopback: (request) => request,
  };
  let publicServerURL: string;
  try {
    const loopback = new URL(loopbackServerURL);
    const publicOrigin = new URL(frontendUrl).origin;
    if (publicOrigin === loopback.origin) return unchanged;
    publicServerURL = `${publicOrigin}${loopback.pathname.replace(/\/+$/, '')}`;
  } catch {
    return unchanged;
  }
  const loopbackBase = loopbackServerURL.replace(/\/+$/, '');
  return {
    serverURL: publicServerURL,
    toLoopback: (request) => {
      // Anything else would carry the caller's bearer token to the public origin.
      if (!request.url.startsWith(publicServerURL)) {
        throw new Error('MCP tool request outside the API base URL');
      }
      return new Request(
        loopbackBase + request.url.slice(publicServerURL.length),
        request,
      );
    },
  };
}
