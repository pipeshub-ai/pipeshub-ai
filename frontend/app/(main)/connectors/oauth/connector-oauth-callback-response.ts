/** Raw Node proxy payload; normalize with {@link parseConnectorOAuthCallbackPayload}. */
export type ConnectorOAuthCallbackRaw = {
  success?: boolean;
  redirectUrl?: string;
  redirect_url?: string;
};

/**
 * Runtime validation for GET `/api/v1/connectors/oauth/callback` (proxied Node response).
 * Accepts legacy snake_case once, then normalizes to camelCase for app code.
 */
export type ParsedConnectorOAuthCallback = {
  success: boolean;
  /** Present when the API returns a redirect target (used to recover connector id). */
  redirectUrl: string | undefined;
};

export function parseConnectorOAuthCallbackPayload(data: unknown): ParsedConnectorOAuthCallback {
  if (data == null || typeof data !== 'object') {
    throw new Error('The server did not confirm OAuth completion.');
  }
  const o = data as Record<string, unknown>;
  const fromCamel = o.redirectUrl;
  const fromSnake = o.redirect_url;
  const redirectUrl =
    typeof fromCamel === 'string' && fromCamel.trim().length > 0
      ? fromCamel.trim()
      : typeof fromSnake === 'string' && fromSnake.trim().length > 0
        ? fromSnake.trim()
        : undefined;

  const s = o.success;
  const successExplicit = s === true || s === 'true' || s === 1 || s === '1';
  const ok = successExplicit || Boolean(redirectUrl);
  if (!ok) {
    throw new Error('The server did not confirm OAuth completion.');
  }

  return {
    success: successExplicit || Boolean(redirectUrl),
    redirectUrl,
  };
}
