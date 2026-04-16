import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { useAuthStore, logoutAndRedirect } from '@/lib/store/auth-store';
import { extractApiErrorMessage, processError } from './api-error';
import { showErrorToast } from './error-toast';

declare module 'axios' {
  export interface AxiosRequestConfig {
    suppressErrorToast?: boolean;
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
const API_TIMEOUT = 20000;

/** Backend signals refresh cannot recover; skip refresh and log out immediately. */
const SESSION_EXPIRED_LOGOUT_MESSAGE = 'Session expired, please login again';

// In-memory lock to prevent multiple simultaneous refresh attempts
let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

// Queue of requests waiting for token refresh
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

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,
});

// Request interceptor - add auth token and request ID
apiClient.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().accessToken;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    } else {
      console.warn('No access token found for authenticated request:', config.url);
    }
    // config.headers['x-request-id'] = generateRequestId();
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
 * Attempts to refresh the access token using the stored refresh token
 */
async function refreshAccessToken(): Promise<boolean> {
  // If already refreshing, return the existing promise
  if (refreshPromise) {
    return refreshPromise;
  }

  refreshPromise = (async () => {
    try {
      const refreshToken = useAuthStore.getState().refreshToken;

      if (!refreshToken) {
        console.log('No refresh token available');
        return false;
      }

      console.log('Attempting token refresh...');

      // Call refresh endpoint - using fetch to avoid interceptor loop
      const response = await fetch(`${API_BASE_URL}/api/v1/userAccount/refresh/token`, {
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

      if (data.accessToken || data.token) {
        const newAccessToken = data.accessToken || data.token;
        const newRefreshToken = data.refresh_token || data.refreshToken || refreshToken;

        // Update auth store
        useAuthStore.getState().setTokens(newAccessToken, newRefreshToken);

        console.log('Token refreshed successfully');
        return true;
      }

      return false;
    } catch (error) {
      console.error('Error refreshing token:', error);
      return false;
    }
  })();

  return refreshPromise;
}

/**
 * Handle authentication failure - clear tokens and redirect to login
 */
function handleAuthFailure(): void {
  logoutAndRedirect();
}

export { apiClient as default };
