import { ConnectorOAuthCallbackClient } from './connector-oauth-callback-client';

/**
 * Connector OAuth callback. IdPs redirect to `/connectors/oauth/callback/<slug>?code=…`;
 * static hosts forward those to `/connectors/oauth/callback/` (see `public/_redirects`
 * and `next.config.mjs` rewrites), mirroring the toolset OAuth callback pattern.
 * The slug is only required for IdP redirect URI parity — the callback reads `code`,
 * `state`, and error params from `window.location.search`.
 */
export default function ConnectorOAuthCallbackPage() {
  return <ConnectorOAuthCallbackClient />;
}
