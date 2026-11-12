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

// Kept on window, not in module state: a hot reload re-runs the module and must replace the listener, not add one.
const SUCCESS_LISTENER_KEY = '__connectorOAuthSuccessListener';
type WindowWithListener = Window & { [SUCCESS_LISTENER_KEY]?: (event: MessageEvent) => void };

/**
 * Calls `onSuccess` whenever a connector OAuth popup reports success to this
 * window, whether or not the panel that opened it is still mounted. One
 * listener per window; registering again replaces it.
 */
export function listenForConnectorOAuthSuccess(onSuccess: () => void): void {
  if (typeof window === 'undefined') return;
  const target = window as WindowWithListener;
  const previous = target[SUCCESS_LISTENER_KEY];
  if (previous) window.removeEventListener('message', previous);
  const listener = (event: MessageEvent) => {
    if (event.origin !== window.location.origin) return;
    if ((event.data as { type?: string } | null)?.type === CONNECTOR_OAUTH_POST_MESSAGE.SUCCESS) onSuccess();
  };
  target[SUCCESS_LISTENER_KEY] = listener;
  window.addEventListener('message', listener);
}
