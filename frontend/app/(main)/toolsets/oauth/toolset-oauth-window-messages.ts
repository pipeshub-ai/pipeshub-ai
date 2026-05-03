/**
 * postMessage contracts between the toolset OAuth popup (`/toolsets/oauth/callback/...`)
 * and the opener (agent builder dialogs, sidebar). Target origin is always the app origin.
 */
export const TOOLSET_OAUTH_POST_MESSAGE = {
  SUCCESS: 'oauth-success',
  SUCCESS_LEGACY: 'TOOLSET_OAUTH_SUCCESS',
  ERROR: 'oauth-error',
  ERROR_LEGACY: 'TOOLSET_OAUTH_ERROR',
} as const;

function openerTargetOrigin(): string {
  return typeof window !== 'undefined' ? window.location.origin : '';
}

export function postToolsetOAuthSuccessToOpener(toolsetType?: string): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage(
      { type: TOOLSET_OAUTH_POST_MESSAGE.SUCCESS, toolsetType },
      origin
    );
    window.opener.postMessage(
      { type: TOOLSET_OAUTH_POST_MESSAGE.SUCCESS_LEGACY, toolsetType },
      origin
    );
  } catch {
    /* ignore */
  }
}

export function postToolsetOAuthErrorToOpener(toolsetType: string | undefined, error: string): void {
  const origin = openerTargetOrigin();
  if (!origin || !window.opener) return;
  try {
    window.opener.postMessage(
      { type: TOOLSET_OAUTH_POST_MESSAGE.ERROR, error, toolsetType },
      origin
    );
    window.opener.postMessage(
      { type: TOOLSET_OAUTH_POST_MESSAGE.ERROR_LEGACY, toolsetType, error },
      origin
    );
  } catch {
    /* ignore */
  }
}

export function isToolsetOAuthSuccessMessageType(type: unknown): boolean {
  return type === TOOLSET_OAUTH_POST_MESSAGE.SUCCESS || type === TOOLSET_OAUTH_POST_MESSAGE.SUCCESS_LEGACY;
}

export function isToolsetOAuthErrorMessageType(type: unknown): boolean {
  return type === TOOLSET_OAUTH_POST_MESSAGE.ERROR || type === TOOLSET_OAUTH_POST_MESSAGE.ERROR_LEGACY;
}
