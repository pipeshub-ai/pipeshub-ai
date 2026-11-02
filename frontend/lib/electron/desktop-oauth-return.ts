import { isDesktopOAuthState, type DesktopIntegrationFamily } from '@/lib/auth/desktop-oauth';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { isElectron } from './is-electron';
import { awaitOAuthDeepLink, DesktopOAuthError } from './oauth-deep-link';

export const DESKTOP_OAUTH_OPEN_FAILED_MESSAGE = 'Could not open your browser. Please try again.';

/** Query params for an integration authorize request, so the backend marks `state` for desktop. */
export function desktopAuthorizeParams(): { desktop?: 'true' } {
  return isElectron() ? { desktop: 'true' } : {};
}

export interface DesktopIntegrationOAuthFlow {
  /** Resolves once `complete` has finished the backend callback with the app's session. */
  promise: Promise<void>;
  cancel: () => void;
}

/**
 * Connector, toolset and MCP OAuth from the desktop app.
 *
 * The desktop shell denies popups, so consent runs in the system browser and the provider
 * redirects that browser to the web callback page. Because the authorize URL was requested
 * by the app, the backend marked its `state`; the page sees the marker and hands `code` and
 * `state` back over a `pipeshub://integration/<family>/callback` deep link instead of
 * calling the API with the browser's session. `complete` then finishes the callback from
 * the app, authenticated as the user who started it.
 */
export function runDesktopIntegrationOAuth(options: {
  family: DesktopIntegrationFamily;
  authUrl: string;
  complete: (callback: { code: string; state: string }) => Promise<void>;
  timeoutMs?: number;
}): DesktopIntegrationOAuthFlow {
  const { family, authUrl, complete, timeoutMs } = options;

  let expectedState: string | null = null;
  try {
    expectedState = new URL(authUrl).searchParams.get('state');
  } catch {
    // Falls through to the unmarked-state error below.
  }
  if (!expectedState || !isDesktopOAuthState(expectedState)) {
    // The server ignored `desktop=true` (an older backend): the callback page would
    // complete in the browser instead of handing back, so waiting could only time out.
    return {
      promise: Promise.reject(
        new DesktopOAuthError(
          'unavailable',
          'This server does not support authorizing integrations from the desktop app yet.',
        ),
      ),
      cancel: () => {},
    };
  }

  const flow = awaitOAuthDeepLink({
    kind: 'integration',
    provider: family,
    authUrl,
    expectedState,
    timeoutMs,
    activity: 'authorization',
  });

  const promise = flow.promise.then(async (params) => {
    if (!params.code) {
      throw new DesktopOAuthError('provider-error', 'No authorization code was returned.');
    }
    await complete({ code: params.code, state: params.state });
  });

  return { promise, cancel: flow.cancel };
}

/** Message to show for a failed desktop integration flow, or null when the user cancelled. */
export function desktopIntegrationErrorMessage(error: unknown): string | null {
  if (error instanceof DesktopOAuthError) {
    switch (error.reason) {
      case 'cancelled':
        return null;
      case 'timeout':
        return 'Authorization timed out. Please try again.';
      case 'open-failed':
        return DESKTOP_OAUTH_OPEN_FAILED_MESSAGE;
      default:
        return error.message || 'Authorization failed. Please try again.';
    }
  }
  return getUserFacingErrorMessage(error, 'Authorization failed. Please try again.');
}
