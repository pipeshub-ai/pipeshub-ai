import axios, { AxiosError, InternalAxiosRequestConfig, isAxiosError } from 'axios';
import { useAuthStore, logoutAndRedirect } from '@/config';
import { ErrorType, extractApiErrorMessage, processError, ProcessedError } from './api-error';
import { showErrorToast } from './error-toast';
import {
  refreshOrSignOut,
  sessionOver,
  accountChanged,
  isTokenExpired,
  isRefreshInProgress,
  REFRESH_TOKEN_ENDPOINT,
} from './token-refresh';
import { captureSession } from './session-cache';
import { sessionUnavailableMessage } from './session-notice';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { applyElectronOverrides } from '@/lib/electron';
import { generateRequestId } from '@/lib/utils/request-id';

declare module 'axios' {
  export interface AxiosRequestConfig {
    /**
     * Skip the global error toast. Pass a predicate to suppress only the
     * failures the caller renders itself, so every other error still gets
     * the backend's message through the generic toast.
     */
    suppressErrorToast?: boolean | ((error: ProcessedError) => boolean);
    /**
     * @internal Set by apiClient itself: the session the request was sent in; an answer that
     * arrives after it ended is dropped. Do not set it or copy it into another request.
     */
    sessionCurrent?: () => boolean;
  }
}

const sessionEnded = (config: { sessionCurrent?: () => boolean } | undefined): boolean =>
  config?.sessionCurrent?.() === false;

/**
 * What a caller gets for an answer that arrives after its session ended: a
 * promise that never settles, so none of its handlers (state writes, toasts)
 * run for the next person. Fresh each time, so the dropped handlers can be
 * garbage collected.
 */
const dropped = (): Promise<never> => new Promise<never>(() => {});

/**
 * The refresh got no answer for its whole retry cycle: the request was not sent (or not
 * replayed), and nothing says the session is over. The "Reconnecting…" notice is showing.
 */
const sessionUnavailable = (error?: AxiosError): ProcessedError => ({
  type: ErrorType.SERVER_ERROR,
  statusCode: 503,
  message: sessionUnavailableMessage(),
  originalError: error,
});

/** What a request made as one account gets instead of being sent as another: a quiet cancel (no toast). */
const cancelledForAccount = (): Promise<never> => {
  const cancelled: ProcessedError = { type: ErrorType.REQUEST_CANCELLED, message: 'Request was cancelled.' };
  return Promise.reject(cancelled);
};

/** Safe methods (RFC 9110 9.2.1): sending one again cannot repeat a change. */
const REPLAYABLE_METHODS = new Set(['get', 'head', 'options']);

/**
 * A 401 the renewed session cannot fix: the server refused this request (a handler or a
 * service behind it said 401), not the session. Shown with the server's words, never read as a sign-out.
 */
const refusedWithSession = (error: AxiosError): ProcessedError => ({
  ...processError(error),
  type: ErrorType.AUTHORIZATION_ERROR,
  message: extractApiErrorMessage(error.response?.data) ?? 'You do not have permission to perform this action.',
});

function rejectShown(
  processedError: ProcessedError,
  config: { suppressErrorToast?: boolean | ((error: ProcessedError) => boolean) } | undefined,
): Promise<never> {
  const suppress = config?.suppressErrorToast;
  const suppressed = typeof suppress === 'function' ? suppress(processedError) : suppress;
  if (!suppressed) {
    showErrorToast(processedError);
  }
  return Promise.reject(processedError);
}

// Default to '' (same origin). A single sentinel avoids `"undefined"` leaking
// into template-built URLs when `NEXT_PUBLIC_API_BASE_URL` is unset.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

const API_TIMEOUT = 90_000;
const SESSION_TOKEN_STORAGE_KEY = 'workspace_session_token';

/** Backend signals refresh cannot recover; skip refresh and log out immediately. */
const SESSION_EXPIRED_LOGOUT_MESSAGE = 'Session expired, please login again';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
    'client-name': 'pipeshub-ai',
  },
  withCredentials: true,
});

// ── Request interceptor ────────────────────────────────────────────────────
//
// Safety-net path: the timer-based scheduler in `token-refresh-scheduler.ts`
// is the primary refresh mechanism, but this interceptor still pre-checks
// expiry on every outbound request and refreshes via the same shared lock
// (`refreshOrSignOut`) when the token slipped past the buffer for any
// reason (e.g. timer was throttled in a backgrounded tab, request was made
// before the scheduler initialized, manual hot-reload during development).
//
// `getApiBaseUrl()` aligns with `token-refresh.ts` and optional user-stored
// backend URL. `applyElectronOverrides` disables cookies under `app://` where
// Bearer tokens are used instead.
apiClient.interceptors.request.use(
  async (config) => {
    // Kept on the 401 retry, which passes the same config back in.
    config.sessionCurrent ??= captureSession();
    config.baseURL = getApiBaseUrl();
    applyElectronOverrides(config);

    // Tag every request so it can be traced across all backend services.
    config.headers['x-request-id'] = generateRequestId();

    // Skip token handling for the refresh endpoint itself to avoid loops.
    if (config.url?.includes(REFRESH_TOKEN_ENDPOINT)) {
      return config;
    }

    const authHeader =
      (config.headers?.Authorization as string | undefined) ??
      (config.headers?.authorization as string | undefined);
    const headerToken = authHeader?.startsWith('Bearer ')
      ? authHeader.substring(7)
      : null;

    const storeToken = useAuthStore.getState().accessToken;
    let accessToken = headerToken ?? storeToken;

    if (accessToken && isTokenExpired(accessToken)) {
      // A session that is over (signed out now, or earlier) drops the request: none of its
      // handlers may write into the reset stores.
      const outcome = await refreshOrSignOut(accessToken);
      if (sessionOver(outcome)) return dropped();
      if (outcome === 'account-changed') return cancelledForAccount();
      if (outcome === 'refreshed') {
        // Read and checked in one step: the store may have changed since refreshOrSignOut checked it.
        const refreshed = useAuthStore.getState().accessToken;
        if (accountChanged(accessToken, refreshed)) return cancelledForAccount();
        accessToken = refreshed;
      }
      // No answer: send with the token we have and let the server decide, unless it has run out.
      else if (isTokenExpired(accessToken, 0)) return rejectShown(sessionUnavailable(), config);
    }

    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`;
    } else if (!headerToken) {
      console.warn('No access token found for authenticated request:', config.url);
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// ── Response interceptor ───────────────────────────────────────────────────
//
// Safety-net for 401s that slipped past the proactive refresh: e.g. a token
// invalidated server-side, or a request that beat the timer to the network.
// All concurrent 401s collapse onto the same in-flight refresh via the
// shared lock in `token-refresh.ts`.
apiClient.interceptors.response.use(
  (response) => (sessionEnded(response.config) ? dropped() : response),
  async (error: AxiosError) => {
    // A rejection from the request interceptor lands here too, with no
    // request config; it is already the error the caller should see.
    if (!isAxiosError(error)) {
      return Promise.reject(error);
    }
    if (error.response?.data instanceof Blob) {
      try {
        const text = await error.response.data.text();
        error.response.data = JSON.parse(text);
      } catch (parseError) {
        console.warn('Failed to parse Blob error response as JSON:', parseError);
      }
    }

    // Neither a toast nor a refresh for the last session: a stale 401 must not sign the next person out.
    // Checked after the Blob decode, which can span a sign-out and the next sign-in.
    if (sessionEnded(error.config)) return dropped();

    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    // The server says the session is over: sign out, whether or not this request was already replayed.
    if (error.response?.status === 401 && extractApiErrorMessage(error.response.data) === SESSION_EXPIRED_LOGOUT_MESSAGE) {
      localStorage.removeItem(SESSION_TOKEN_STORAGE_KEY);
      sessionStorage.clear();
      logoutAndRedirect();
      return dropped();
    }

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;

      // If a refresh is already running (timer or another 401), this just
      // awaits the existing promise; otherwise it kicks off a new one.
      const sentAuth = originalRequest.headers?.Authorization;
      const sentWith = typeof sentAuth === 'string' && sentAuth.startsWith('Bearer ') ? sentAuth.substring(7) : null;
      const outcome = await refreshOrSignOut(sentWith);
      if (outcome === 'unreachable') return rejectShown(sessionUnavailable(error), originalRequest);
      if (outcome === 'account-changed') return cancelledForAccount();
      if (outcome !== 'refreshed') return dropped();
      // A write is never sent twice: the server may have acted on it before it answered 401.
      if (!REPLAYABLE_METHODS.has(originalRequest.method ?? 'get')) {
        return rejectShown(refusedWithSession(error), originalRequest);
      }
      const newToken = useAuthStore.getState().accessToken;
      // Read and checked in one step: the store may have changed since refreshOrSignOut checked it.
      if (accountChanged(sentWith, newToken)) return cancelledForAccount();
      if (newToken) {
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
      }
      return apiClient(originalRequest);
    }

    // A 401 here came back with the token the refresh just issued, so it is not about the session.
    const processed = error.response?.status === 401 ? refusedWithSession(error) : processError(error);
    return rejectShown(processed, originalRequest);
  },
);

// `isRefreshInProgress` is exported for callers that want to coordinate with
// the shared lock without triggering a new refresh themselves.
export { isRefreshInProgress };

export { apiClient as default };
