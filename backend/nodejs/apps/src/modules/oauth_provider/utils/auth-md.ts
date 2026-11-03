/**
 * `/auth.md`: how an agent gets and uses a credential for this instance, as
 * the Markdown "skill" the auth.md convention describes. It covers only what
 * this instance offers today; it does not advertise the agentic-registration
 * endpoints (`/agent/identity`, the claim grant), which are not implemented.
 */

export interface AuthMdInfo {
  /** The instance origin, e.g. https://pipeshub.example.com. */
  origin: string;
  /** The first-party device app's client id, or null when device login is off. */
  deviceClientId: string | null;
  /** Whether MCP clients may register themselves (Dynamic Client Registration). */
  dcrEnabled: boolean;
  /** The scopes an agent sign-in is granted on this instance. */
  agentScopes: readonly string[];
  /** Whether a device sign-in also gets `offline_access`, so it can refresh. */
  deviceRefreshes: boolean;
  /** Extra sections an edition adds after the ways in, as Markdown. */
  extraSections: readonly string[];
}

export function renderAuthMd(info: AuthMdInfo): string {
  const { origin } = info;
  const oauth = `${origin}/api/v1/oauth2`;
  const ways: string[] = [];

  // Explicit, so the grant is exactly what this page lists (the device app's stored preset can be wider).
  const deviceScope = [
    ...info.agentScopes,
    ...(info.deviceRefreshes ? ['offline_access'] : []),
  ].join(' ');

  if (info.deviceClientId) {
    ways.push(`### Sign in as the person (device code, RFC 8628)

The person approves in a browser; you never see their password.

With the PipesHub CLI:

\`\`\`bash
npx -y -p @pipeshub-ai/mcp pipeshub login --url ${origin} --no-wait
# show the person the link and code it prints, then:
npx -y -p @pipeshub-ai/mcp pipeshub login --wait
\`\`\`

The CLI stores the sign-in (\`~/.config/pipeshub/credentials.json\`, mode 0600) and refreshes it. \`npx -y @pipeshub-ai/mcp start\` uses it, so an MCP client config needs no token.

Without the CLI: \`POST ${oauth}/device_authorization\` with \`client_id=${info.deviceClientId}\` and \`scope=${deviceScope}\` (the agent scopes below${info.deviceRefreshes ? ', plus `offline_access` so it can refresh' : ''}), show the person \`verification_uri_complete\` (or \`verification_uri\` and \`user_code\`), then poll \`POST ${oauth}/token\` with \`grant_type=urn:ietf:params:oauth:grant-type:device_code\`, \`device_code\` and \`client_id\` every \`interval\` seconds until it returns an access token. Until the person approves, that call answers HTTP 400 with \`error: authorization_pending\`: keep polling. On \`slow_down\`, add 5 seconds to the interval. Stop on \`access_denied\` or \`expired_token\`.`);
  }

  if (info.dcrEnabled) {
    ways.push(`### Register an MCP client (OAuth, Dynamic Client Registration)

MCP clients that support OAuth register at \`${oauth}/register\` and sign the person in with the authorization code flow and PKCE. Point the client at \`${origin}/mcp\`; it discovers the rest.`);
  }

  ways.push(`### A personal access token

The person creates one in **Workspace → Developer settings → Personal access tokens** and puts it in your environment as \`PIPESHUB_TOKEN\` (with \`PIPESHUB_BASE_URL=${origin}\`). Never ask them to paste it into the chat.`);

  // Refresh tokens rotate, and reusing a spent one revokes the whole sign-in.
  const refresh =
    'A device sign-in comes with a refresh token: keep the `refresh_token` from the device-code response, then `POST ' +
    `${oauth}/token\` with \`grant_type=refresh_token\`, \`refresh_token\` and \`client_id\`` +
    '. Each refresh returns a new `refresh_token`: store it in place of the old one, which stops working (sending it again ends the sign-in). The CLI does this for you. Otherwise sign';

  return `# PipesHub: authentication for agents

You are an agent. This is how to get a credential for the PipesHub at ${origin} and use it. Never ask the person to paste a token into the chat, and never print, log or commit one.

## 1. Discover

- MCP endpoint: \`${origin}/mcp\`. A call without a credential answers \`401\` with \`WWW-Authenticate: Bearer resource_metadata="${origin}/.well-known/oauth-protected-resource/mcp"\`.
- Authorization server metadata: \`${origin}/.well-known/oauth-authorization-server\`.

## 2. Get a credential

Pick the first that fits.

${[...ways, ...info.extraSections].join('\n\n')}

## 3. Use it

Send \`Authorization: Bearer <token>\` to \`${origin}/mcp\` (MCP) or \`${origin}/api/v1/...\` (REST).

An agent sign-in is granted these scopes: ${info.agentScopes.map((s) => `\`${s}\``).join(', ')}.

## 4. When a call is refused

- \`401\`: the token expired or was revoked. ${info.deviceRefreshes ? refresh : 'Sign'} in again or ask the person for a new token.
- \`403\` with \`Insufficient scope\`: the token lacks the scope it names. The person needs a new token with it; do not try another route to the same data.
`;
}
