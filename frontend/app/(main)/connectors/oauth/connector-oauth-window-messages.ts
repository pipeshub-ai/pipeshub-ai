/**
 * postMessage contracts between the connector OAuth callback page
 * (`/connectors/oauth/callback/...`) and the opener (workspace connector panel).
 *
 * Types are namespaced so they never collide with generic auth popups
 * (`OAUTH_SUCCESS` from `/auth/oauth/callback`).
 */
export const CONNECTOR_OAUTH_POST_MESSAGE = {
  SUCCESS: 'CONNECTOR_OAUTH_SUCCESS',
  ERROR: 'CONNECTOR_OAUTH_ERROR',
} as const;

function openerTargetOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : '';
}

export function postConnectorOAuthSuccessToOpener(connectorId: string | null): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage(
      { type: CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS, connectorId },
      origin
    );
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.debug('[connector-oauth] postMessage success failed', e);
    }
  }
}

export function postConnectorOAuthErrorToOpener(error: string): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage(
      { type: CONNECTOR_OAUTH_POST_MESSAGE.ERROR, error },
      origin
    );
  } catch (e) {
    if (process.env.NODE_ENV === 'development') {
      console.debug('[connector-oauth] postMessage error failed', e);
    }
  }
}
