import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import {
  useAuthStore,
  logoutAndRedirect,
  ACCESS_TOKEN_STORAGE_KEY,
  REFRESH_TOKEN_STORAGE_KEY,
} from '@/lib/store/auth-store';
import { extractApiErrorMessage, processError } from './api-error';
import { showErrorToast } from './error-toast';

declare module 'axios' {
  export interface AxiosRequestConfig {
    suppressErrorToast?: boolean;
  }
}

// Default to '' (same origin). Axios itself treats undefined baseURL the same
// way, but `refreshAccessToken()` below does a raw `fetch(\`${API_BASE_URL}...\`)`
// — template-concatenating `undefined` would produce the literal string
// "undefined" in the URL and 404 via the Node.js backend's static handler.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

const API_TIMEOUT = 90_000;

/** Backend signals refresh cannot recover; skip refresh and log out immediately. */
const SESSION_EXPIRED_LOGOUT_MESSAGE = 'Session expired, please login again';

/** Endpoints that must never go through proactive refresh (avoid infinite loops). */
const REFRESH_TOKEN_ENDPOINT = '/api/v1/userAccount/refresh/token';

// In-memory lock to prevent multiple simultaneous refresh attempts
let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

// Queue of requests waiting for token refresh (response-interceptor path)
let failedQueue: Array<{
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
  config: InternalAxiosRequestConfig;
}> = [];

const processQueue = (error: Error | null, token: string | null = null) => {
  failedQueue.forEach((request) => {
    if (error) {
      request.reject(error);
    } else if (token) {
      request.config.headers.Authorization = `Bearer ${token}`;
      request.resolve(apiClient(request.config));
    }
  });
  failedQueue = [];
};

interface JwtPayload {
  exp?: number;
  [key: string]: unknown;
}

/** Decodes a JWT payload without verifying the signature. */
function decodeToken(token: string | null): JwtPayload | null {
  try {
    if (!token) return null;
    const parts = token.split('.');
    if (parts.length < 2) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = typeof atob === 'function' ? atob(base64) : '';
    if (!payload) return null;
    return JSON.parse(payload) as JwtPayload;
  } catch {
    return null;
  }
}

/** True when the token has an `exp` claim in the past. */
function isTokenExpired(token: string | null): boolean {
  const decoded = decodeToken(token);
  if (!decoded || typeof decoded.exp !== 'number') return false;
  const nowSeconds = Date.now() / 1000;
  return decoded.exp < nowSeconds + 90;
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

// Request interceptor - add auth token, proactively refresh if expired.
apiClient.interceptors.request.use(
  async (config) => {
    // Skip token handling for the refresh endpoint itself to avoid loops.
    if (config.url?.includes(REFRESH_TOKEN_ENDPOINT)) {
      return config;
    }

    // Allow callers to pre-set their own Authorization header.
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
        // Refresh failed - clear auth and redirect.
        handleAuthFailure();
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
  (error) => Promise.reject(error)
);

// Response interceptor - handle errors globally
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    // Handle 401 - session explicitly ended on server, or attempt token refresh
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      const apiMessage = extractApiErrorMessage(error.response.data);
      if (apiMessage === SESSION_EXPIRED_LOGOUT_MESSAGE) {
        processQueue(new Error(SESSION_EXPIRED_LOGOUT_MESSAGE), null);
        isRefreshing = false;
        refreshPromise = null;
        handleAuthFailure();
        const processedError = processError(error);
        return Promise.reject(processedError);
      }

      if (isRefreshing) {
        // If already refreshing, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject, config: originalRequest });
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const refreshSuccess = await refreshAccessToken();

        if (refreshSuccess) {
          const newToken = useAuthStore.getState().accessToken;
          processQueue(null, newToken);

          // Retry the original request with new token
          if (newToken) {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
          }
          return apiClient(originalRequest);
        } else {
          // Refresh failed - logout and redirect
          processQueue(new Error('Token refresh failed'), null);
          handleAuthFailure();
          const processedError = processError(error);
          return Promise.reject(processedError);
        }
      } catch {
        processQueue(new Error('Token refresh failed'), null);
        handleAuthFailure();
        const processedError = processError(error);
        return Promise.reject(processedError);
      } finally {
        isRefreshing = false;
        refreshPromise = null;
      }
    }

    // Process and reject error for other cases
    const processedError = processError(error);

    // Show persistent error toast for non-auth errors unless suppressed by caller
    if (!originalRequest.suppressErrorToast) {
      showErrorToast(processedError);
    }

    return Promise.reject(processedError);
  }
);

/**
 * Attempts to refresh the access token using the stored refresh token.
 * Reads the refresh token from localStorage (fallback) as well as the
 * auth store, so behavior matches the legacy frontend.
 */
async function refreshAccessToken(): Promise<boolean> {
  // If already refreshing, return the existing promise
  if (refreshPromise) {
    return refreshPromise;
  }

  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      const refreshToken =
        useAuthStore.getState().refreshToken ??
        (typeof window !== 'undefined'
          ? window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)
          : null);

      if (!refreshToken) {
        console.log('No refresh token available');
        return false;
      }

      // Call refresh endpoint - using fetch to avoid interceptor loop
      const response = await fetch(`${API_BASE_URL}${REFRESH_TOKEN_ENDPOINT}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${refreshToken}`,
        },
      });

      if (!response.ok) {
        console.log('Token refresh request failed:', response.status);
        return false;
      }

      const data = await response.json();
      const newAccessToken: string | undefined = data.accessToken || data.token;
      const newRefreshToken: string =
        data.refresh_token || data.refreshToken || refreshToken;

      if (newAccessToken) {
        useAuthStore.getState().setTokens(newAccessToken, newRefreshToken);
        // Keep legacy localStorage key in sync for callers that read it directly.
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, newAccessToken);
        }
        return true;
      }

      return false;
    } catch (error) {
      console.error('Error refreshing token:', error);
      return false;
    } finally {
      isRefreshing = false;
    }
  })();

  try {
    return await refreshPromise;
  } finally {
    refreshPromise = null;
  }
}

/**
 * Handle authentication failure - clear tokens and redirect to login
 */
function handleAuthFailure(): void {
  logoutAndRedirect();
}

export { apiClient as default };
