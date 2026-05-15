import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { useAuthStore, logoutAndRedirect } from '@/lib/store/auth-store';
import { extractApiErrorMessage, processError } from './api-error';
import { showErrorToast } from './error-toast';
import {
  refreshAccessToken,
  isTokenExpired,
  isRefreshInProgress,
  REFRESH_TOKEN_ENDPOINT,
} from './token-refresh';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { applyElectronOverrides } from '@/lib/electron';

declare module 'axios' {
  export interface AxiosRequestConfig {
    suppressErrorToast?: boolean;
  }
}

// Default to '' (same origin). A single sentinel avoids `"undefined"` leaking
// into template-built URLs when `NEXT_PUBLIC_API_BASE_URL` is unset.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

const API_TIMEOUT = 90_000;

/** Backend signals refresh cannot recover; skip refresh and log out immediately. */
const SESSION_EXPIRED_LOGOUT_MESSAGE = 'Session expired, please login again';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

// ── Request interceptor ────────────────────────────────────────────────────
//
// Safety-net path: the timer-based scheduler in `token-refresh-scheduler.ts`
// is the primary refresh mechanism, but this interceptor still pre-checks
// expiry on every outbound request and refreshes via the same shared lock
// (`refreshAccessToken`) when the token slipped past the buffer for any
// reason (e.g. timer was throttled in a backgrounded tab, request was made
// before the scheduler initialized, manual hot-reload during development).
//
// `getApiBaseUrl()` aligns with `token-refresh.ts` and optional user-stored
// backend URL. `applyElectronOverrides` disables cookies under `app://` where
// Bearer tokens are used instead.
apiClient.interceptors.request.use(
  async (config) => {
    config.baseURL = getApiBaseUrl();
    applyElectronOverrides(config);

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
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        accessToken = useAuthStore.getState().accessToken;
      } else {
        logoutAndRedirect();
        return Promise.reject(new Error(SESSION_EXPIRED_LOGOUT_MESSAGE));
      }
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
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.data instanceof Blob) {
      try {
        const text = await error.response.data.text();
        error.response.data = JSON.parse(text);
      } catch (parseError) {
        console.warn('Failed to parse Blob error response as JSON:', parseError);
      }
    }

    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      const apiMessage = extractApiErrorMessage(error.response.data);
      if (apiMessage === SESSION_EXPIRED_LOGOUT_MESSAGE) {
        logoutAndRedirect();
        return Promise.reject(processError(error));
      }

      originalRequest._retry = true;

      try {
        // If a refresh is already running (timer or another 401), this just
        // awaits the existing promise; otherwise it kicks off a new one.
        const refreshSuccess = await refreshAccessToken();

        if (refreshSuccess) {
          const newToken = useAuthStore.getState().accessToken;
          if (newToken) {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
          }
          return apiClient(originalRequest);
        }

        logoutAndRedirect();
        return Promise.reject(processError(error));
      } catch {
        logoutAndRedirect();
        return Promise.reject(processError(error));
      }
    }

    const processedError = processError(error);

    if (!originalRequest.suppressErrorToast) {
      showErrorToast(processedError);
    }

    return Promise.reject(processedError);
  },
);

// `isRefreshInProgress` is exported for callers that want to coordinate with
// the shared lock without triggering a new refresh themselves.
export { isRefreshInProgress };

export { apiClient as default };
