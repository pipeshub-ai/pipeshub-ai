/**
 * Desktop half of the browser-based OAuth sign-in flow.
 *
 * No provider will accept `app://./auth/<provider>/callback` as a redirect
 * target, so the authorize URL opens in the user's default browser and
 * redirects to the ordinary web callback on the server origin — the same URI
 * the web app already has registered. That page hands the result back through
 * a `pipeshub://` deep link, which the main process forwards here.
 *
 * The browser-safe half of the contract lives in `@/lib/auth/desktop-oauth`,
 * because the callback page that builds the deep link is not running in Electron.
 */

import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { getFrontendOrigin, type DesktopOAuthProvider } from '@/lib/auth/desktop-oauth';
import { isElectron } from './is-electron';

/** Matches the payload main sends on `oauth/callback` (see electron/deep-link.ts). */
export interface OAuthDeepLinkPayload {
  provider: DesktopOAuthProvider;
  params: Record<string, string>;
  receivedAt: number;
}

/** Flat rather than a discriminated union: this crosses an IPC boundary, so
 *  the renderer cannot assume the shape, and the project builds with
 *  `strict: false`, where narrowing on a boolean discriminant is unreliable. */
interface TokenExchangeResult {
  ok: boolean;
  status?: number;
  body?: string;
  error?: string;
}

interface OAuthBridge {
  openExternal(url: string): Promise<{ ok: boolean; error?: string }>;
  consumePending(): Promise<OAuthDeepLinkPayload | null>;
  onCallback(callback: (payload: unknown) => void): () => void;
  exchangeToken(payload: { url: string; body: string; origin?: string }): Promise<TokenExchangeResult>;
}

function getOAuthBridge(): OAuthBridge | null {
  if (typeof window === 'undefined' || !isElectron()) return null;
  const api = (window as unknown as { electronAPI?: { oauth?: OAuthBridge } }).electronAPI;
  return api?.oauth ?? null;
}

export type DesktopOAuthFailure =
  | 'unavailable'
  | 'no-server-url'
  | 'open-failed'
  | 'timeout'
  | 'cancelled'
  | 'provider-error';

export class DesktopOAuthError extends Error {
  constructor(readonly reason: DesktopOAuthFailure, message: string) {
    super(message);
    this.name = 'DesktopOAuthError';
  }
}

/**
 * `<frontend origin>/auth/<provider>/callback` — byte-identical to what the
 * web app sends, which is why no OAuth client registration has to change.
 *
 * Prefers the origin the backend reported over the API base URL the user
 * typed. Those are the same host only when one process serves both, and the
 * backend redeems GitHub's code against its own configured value.
 */
export function buildDesktopRedirectUri(provider: DesktopOAuthProvider): string {
  const base = getFrontendOrigin() ?? getApiBaseUrl().trim().replace(/\/+$/, '');
  if (!base) {
    throw new DesktopOAuthError('no-server-url', 'No PipesHub server URL is configured.');
  }
  return `${base}/auth/${provider}/callback`;
}

/** Long enough for a real sign-in including MFA, short enough to not wait forever. */
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

export interface DesktopOAuthFlow {
  /** Resolves with the provider's returned params, keyed as the provider sent them. */
  promise: Promise<Record<string, string>>;
  cancel: () => void;
}

/**
 * Open the authorize URL in the browser and wait for the deep link to come back.
 *
 * `expectedState` is held in this closure rather than read back from storage,
 * so a forged link cannot be aligned by writing to the renderer's localStorage.
 */
export function runDesktopOAuth(options: {
  provider: DesktopOAuthProvider;
  authUrl: string;
  expectedState: string;
  timeoutMs?: number;
}): DesktopOAuthFlow {
  const { provider, authUrl, expectedState, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  let cancel = (): void => {};

  const promise = new Promise<Record<string, string>>((resolve, reject) => {
    const bridge = getOAuthBridge();
    if (!bridge) {
      reject(new DesktopOAuthError('unavailable', 'Desktop sign-in is unavailable in this session.'));
      return;
    }

    let settled = false;
    let unsubscribe = (): void => {};
    let timer: ReturnType<typeof setTimeout> | null = null;

    const done = (outcome: () => void): void => {
      if (settled) return;
      settled = true;
      unsubscribe();
      if (timer) clearTimeout(timer);
      outcome();
    };

    const consider = (raw: unknown): void => {
      const payload = raw as OAuthDeepLinkPayload | null;
      if (!payload || payload.provider !== provider) return;
      // Anything whose state is not the one this flow generated is not ours:
      // a stale attempt, or a link replayed by another local app. Ignore it
      // rather than failing, so it cannot be used to kill a real attempt.
      if (payload.params?.state !== expectedState) return;

      const error = payload.params.error;
      if (error) {
        const description = payload.params.error_description;
        done(() => reject(new DesktopOAuthError('provider-error', description || error)));
        return;
      }
      done(() => resolve(payload.params));
    };

    unsubscribe = bridge.onCallback(consider);
    timer = setTimeout(() => {
      done(() => reject(new DesktopOAuthError('timeout', 'Timed out waiting for browser sign-in.')));
    }, timeoutMs);
    cancel = () => done(() => reject(new DesktopOAuthError('cancelled', 'Sign-in cancelled.')));

    // Covers the gap where main delivered the link before this listener
    // attached, which is the normal case on a cold start.
    void bridge.consumePending().then(consider).catch(() => {
      // A failed drain is not fatal; the live listener still covers the flow.
    });

    void bridge
      .openExternal(authUrl)
      .then((result) => {
        if (result?.ok) return;
        done(() =>
          reject(new DesktopOAuthError('open-failed', result?.error || 'Could not open your browser.')),
        );
      })
      .catch(() => {
        done(() => reject(new DesktopOAuthError('open-failed', 'Could not open your browser.')));
      });
  });

  return { promise, cancel: () => cancel() };
}

/**
 * Redeem an authorization code through the main process, returning an ordinary
 * Response so callers read it exactly as they read a fetch.
 *
 * Microsoft redeems a single-page-application code only cross-origin
 * (AADSTS9002327), so the request has to carry the Origin that app
 * registration lists. A renderer cannot send it: its origin is app://, and
 * fetch will not let a caller override Origin.
 */
export async function exchangeOAuthTokenViaMain(payload: {
  url: string;
  body: string;
  origin?: string;
}): Promise<Response> {
  const bridge = getOAuthBridge();
  if (!bridge) {
    throw new DesktopOAuthError('unavailable', 'Desktop token exchange is unavailable in this session.');
  }
  const result = await bridge.exchangeToken(payload);
  if (!result || !result.ok) {
    throw new Error(result?.error || 'Token exchange failed.');
  }
  return new Response(result.body ?? '', { status: result.status ?? 200 });
}

/**
 * Message for the sign-in button's error banner. Returns null when the user
 * cancelled, which needs no banner.
 */
export function desktopOAuthErrorMessage(error: unknown, providerLabel: string): string | null {
  if (error instanceof DesktopOAuthError) {
    switch (error.reason) {
      case 'cancelled':
        return null;
      case 'timeout':
        return `${providerLabel} sign-in timed out. Please try again.`;
      case 'no-server-url':
        return 'Connect to a PipesHub server before signing in.';
      case 'open-failed':
        return 'Could not open your browser. Please try again.';
      case 'unavailable':
        return `${providerLabel} sign-in is unavailable in this app.`;
      default:
        return error.message || `${providerLabel} sign-in failed. Please try again.`;
    }
  }
  if (error instanceof Error && error.message) return error.message;
  return `${providerLabel} sign-in failed. Please try again.`;
}
