import { ConnectorOAuthCallbackClient } from './connector-oauth-callback-client';

/**
 * Connector OAuth callback (per-connector slug in path for IdP redirect URI parity).
 * Example: `/connectors/oauth/callback/Drive`
 */
export default function ConnectorOAuthCallbackPage() {
  return <ConnectorOAuthCallbackClient />;
}
