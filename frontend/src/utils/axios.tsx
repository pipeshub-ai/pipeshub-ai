import type { ReactNode } from 'react';
import type { AxiosRequestConfig } from 'axios';

import axios from 'axios';
import React, {
  useMemo,
  useState,
  useEffect,
  useContext,
  useCallback,
  createContext,
} from 'react';

import { Alert, Snackbar } from '@mui/material';

import { CONFIG } from 'src/config-global';
import { STORAGE_KEY, STORAGE_KEY_REFRESH } from 'src/auth/context/jwt/constant';

function decodeToken(token: string | null) {
  try {
    if (!token) return null;

    const parts = token.split('.');
    if (parts.length < 2) {
      return null;
    }

    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const decoded = JSON.parse(atob(base64));

    return decoded;
  } catch (error) {
    return null;
  }
}

let isRefreshing = false;
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }

  const refreshToken = localStorage.getItem(STORAGE_KEY_REFRESH);
  if (!refreshToken) {
    return null;
  }

  isRefreshing = true;
  refreshPromise = axios
    .post(
      `${CONFIG.authUrl}/api/v1/userAccount/refresh/token`,
      {},
      {
        headers: {
          Authorization: `Bearer ${refreshToken}`,
        },
      }
    )
    .then((res) => {
      const newAccessToken = res.data.accessToken;
      if (newAccessToken) {
        localStorage.setItem(STORAGE_KEY, newAccessToken);
      }
      return newAccessToken as string;
    })
    .catch((error) => {
      console.error('Failed to refresh access token:', error);
      return null;
    })
    .finally(() => {
      isRefreshing = false;
      refreshPromise = null;
    });

  return refreshPromise;
}

// Error types for better classification
export enum ErrorType {
  SERVER_ERROR = 'SERVER_ERROR', // 5xx errors
  AUTHENTICATION_ERROR = 'AUTHENTICATION_ERROR', // 401, 403 errors
  VALIDATION_ERROR = 'VALIDATION_ERROR', // 400 errors with validation issues
  NOT_FOUND_ERROR = 'NOT_FOUND_ERROR', // 404 errors
  NETWORK_ERROR = 'NETWORK_ERROR', // Connection issues
  TIMEOUT_ERROR = 'TIMEOUT_ERROR', // Request timeout
  UNKNOWN_ERROR = 'UNKNOWN_ERROR', // Fallback for other errors
}

// Standardized error response
export interface ProcessedError {
  type: ErrorType;
  message: string;
  statusCode?: number;
  details?: Record<string, any>;
  retry?: boolean; // Flag indicating if this error can be retried
}

// Context for error handling and snackbar
interface ErrorContextType {
  showError: (message: string) => void;
}

const ErrorContext = createContext<ErrorContextType | null>(null);

export const useError = (): ErrorContextType => {
  const context = useContext(ErrorContext);
  if (!context) {
    throw new Error('useError must be used within an ErrorProvider');
  }
  return context;
};

interface ErrorProviderProps {
  children: ReactNode;
}

// Create axios instance with config
const axiosInstance = axios.create({ baseURL: CONFIG.backendUrl });

// Request interceptor to check token validity and refresh if needed
axiosInstance.interceptors.request.use(
  async (config) => {
    // Skip token check for refresh token endpoint to avoid infinite loop
    if (config.url?.includes('/refresh/token')) {
      return config;
    }

    // Extract token from Authorization header if present
    const authHeader = config.headers?.Authorization as string | undefined;
    const accessToken = authHeader?.startsWith('Bearer ') 
      ? authHeader.substring(7) 
      : localStorage.getItem(STORAGE_KEY);
    
    if (accessToken) {
      try {
        // Decode token to check expiration
        const decoded = decodeToken(accessToken);
        
        if (decoded && 'exp' in decoded) {
          const currentTime = Date.now() / 1000;
          
          // Only refresh if token is expired
          if (decoded.exp < currentTime) {
            // Token is expired, try to refresh
            const newAccessToken = await refreshAccessToken();
            
            if (newAccessToken) {
              // Token was successfully refreshed
              config.headers.Authorization = `Bearer ${newAccessToken}`;
            } else {
              // Token refresh failed, clear storage and redirect to login
              localStorage.removeItem(STORAGE_KEY);
              localStorage.removeItem(STORAGE_KEY_REFRESH);
              window.location.href = '/auth/sign-in';
              throw new Error('Session expired, please login again');
            }
          } else if (!authHeader) {
            // Token is still valid, add it to the header
            config.headers.Authorization = `Bearer ${accessToken}`;
          }
        }
      } catch (error) {
        // If token decode fails, clear storage and redirect
        localStorage.removeItem(STORAGE_KEY);
        localStorage.removeItem(STORAGE_KEY_REFRESH);
        window.location.href = '/auth/sign-in';
        throw new Error('Invalid token');
      }
    }
    
    return config;
  },
  (error) => Promise.reject(error)
);

// Enhanced error handling in interceptor
axiosInstance.interceptors.response.use(
  (response) => response,
  (error) => {
    // Default error structure
    const processedError: ProcessedError = {
      type: ErrorType.UNKNOWN_ERROR,
      message:
        error?.response?.data?.error?.message || 'Something went wrong. Please try again later.',
      retry: false,
    };

    // Remove noisy debug logs in production

    // Axios error with response from server
    if (axios.isAxiosError(error)) {
      // Connection or timeout errors (no response)
      if (!error.response) {
        if (error.code === 'ECONNABORTED') {
          processedError.type = ErrorType.TIMEOUT_ERROR;
          processedError.message = 'Request timed out. Please try again.';
          processedError.retry = true;
        } else if (error.message && error.message.includes('Network Error')) {
          processedError.type = ErrorType.NETWORK_ERROR;
          processedError.message =
            'Unable to connect to server. Please check your internet connection.';
          processedError.retry = true;
        }
      }
      // Server responded with an error status
      else if (error.response) {
        processedError.statusCode = error.response.status;

        // Set message and details from response if available
        const data: any = error.response.data;
        if (data) {
          if (typeof data === 'string') {
            processedError.message = data;
          } else {
            // Prefer explicit message if present
            if (typeof data.message === 'string' && data.message.trim()) {
              processedError.message = data.message;
            }
            // Validation/issue arrays (e.g., Zod)
            const issues = data.issues || data.error?.issues || data.errors;
            if (Array.isArray(issues) && issues.length > 0) {
              const first = issues[0];
              const issueMsg = (first?.message || first)?.toString?.() || '';
              const path = first?.path ? (Array.isArray(first.path) ? first.path.join('.') : String(first.path)) : '';
              const combined = path ? `${issueMsg} (${path})` : issueMsg;
              if (combined) processedError.message = combined;
            }
            // Validation errors from our middleware: error.metadata.errors
            const metaErrors = data.error?.metadata?.errors;
            if (Array.isArray(metaErrors) && metaErrors.length > 0) {
              const first = metaErrors[0];
              const msg = first?.message || '';
              if (msg) processedError.message = msg;
              processedError.details = {
                ...(processedError.details || {}),
                validationErrors: metaErrors,
              };
            }
            // error.metadata.detail from our backend
            if (data.error?.metadata?.detail) {
              processedError.message = data.error.metadata.detail;
            }
            // error.message fallback
            if (!processedError.message && data.error?.message) {
              processedError.message = data.error.message;
            }
            // If backend provides an error.code, set retry false and keep code for consumers
            if (data.error?.code && typeof data.error.code === 'string') {
              processedError.details = { ...(processedError.details || {}), code: data.error.code };
              processedError.retry = false;
            }
            // details/reason fallbacks
            if (!processedError.message && typeof data.details === 'string') {
              processedError.message = data.details;
            }
            if (!processedError.message && typeof data.reason === 'string') {
              processedError.message = data.reason;
            }
            // Store details object if present
            if (data.error && typeof data.error === 'object') {
              processedError.details = data.error;
            }
          }
        }

        // Categorize by status code
        if (error.response.status >= 500) {
          processedError.type = ErrorType.SERVER_ERROR;
          processedError.message =
            processedError.message || 'The server encountered an error. Please try again later.';
          processedError.retry = true;
        } else if (error.response.status === 401 || error.response.status === 403) {
          processedError.type = ErrorType.AUTHENTICATION_ERROR;
          processedError.message =
            processedError.message || 'Authentication failed. Please sign in again.';
          // Check for specific message to trigger logout
          if (error.response.status === 401) {
            const responseMessage = processedError.message as string;
            // Check for session expired or similar messages
            if (responseMessage === "Session expired, please login again") {
              localStorage.removeItem(STORAGE_KEY);
              localStorage.removeItem(STORAGE_KEY_REFRESH);
              // Redirect to login page
              window.location.href = '/auth/sign-in';
            }
          }
        } else if (error.response.status === 404) {
          processedError.type = ErrorType.NOT_FOUND_ERROR;
          processedError.message =
            processedError.message || 'The requested resource was not found.';
        } else if (error.response.status === 400) {
          processedError.type = ErrorType.VALIDATION_ERROR;
          processedError.message =
            processedError.message || 'Invalid input data. Please check and try again.';
        }
      }
    }
    // Handle non-axios errors
    else if (error instanceof Error) {
      processedError.message = error.message;
    }
    

    // Try to show error in snackbar if ErrorContext is available
    try {
      const errorContext = window.__errorContext;
      if (errorContext && errorContext.showError) {
        errorContext.showError(processedError.message);
      }
    } catch (e) {
      console.error('Failed to show error in snackbar:', e);
    }

    return Promise.reject(processedError);
  }
);

// Error provider component that provides snackbar functionality
export const ErrorProvider: React.FC<ErrorProviderProps> = ({ children }) => {
  const [snackbarOpen, setSnackbarOpen] = useState(false);
  const [snackbarMessage, setSnackbarMessage] = useState('');

  const showError = useCallback((message: string) => {
    setSnackbarMessage(message);
    setSnackbarOpen(true);
  }, []);

  const handleClose = (_event?: React.SyntheticEvent | Event, reason?: string) => {
    if (reason === 'clickaway') {
      return;
    }
    setSnackbarOpen(false);
  };

  const contextValue = useMemo(() => ({ showError }), [showError]);

  // Make error handler available globally
  useEffect(() => {
    window.__errorContext = contextValue;
    return () => {
      delete window.__errorContext;
    };
  }, [contextValue]);

  return (
    <ErrorContext.Provider value={contextValue}>
      {children}
      <Snackbar
        open={snackbarOpen}
        autoHideDuration={10000}
        onClose={handleClose}
        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Alert onClose={handleClose} severity="error" sx={{ width: '100%' }}>
          {snackbarMessage}
        </Alert>
      </Snackbar>
    </ErrorContext.Provider>
  );
};

export default axiosInstance;

// ----------------------------------------------------------------------

export const fetcher = async (args: string | [string, AxiosRequestConfig]) => {
  try {
    const [url, config] = Array.isArray(args) ? args : [args];
    const res = await axiosInstance.get(url, { ...config });
    return res.data;
  } catch (error) {
    console.error('Failed to fetch:', error);
    throw error;
  }
};

// ----------------------------------------------------------------------

export const endpoints = {
  chat: '/api/chat',
  kanban: '/api/kanban',
  calendar: '/api/calendar',
  auth: {
    me: '/api/auth/me',
    signIn: '/api/auth/sign-in',
    signUp: '/api/auth/sign-up',
  },
  mail: {
    list: '/api/mail/list',
    details: '/api/mail/details',
    labels: '/api/mail/labels',
  },
  post: {
    list: '/api/post/list',
    details: '/api/post/details',
    latest: '/api/post/latest',
    search: '/api/post/search',
  },
  product: {
    list: '/api/product/list',
    details: '/api/product/details',
    search: '/api/product/search',
  },
};

// Helper function to wrap API calls with error handling
export async function withErrorHandling<T>(
  apiCall: () => Promise<T>,
  errorCallback?: (error: ProcessedError) => void
): Promise<T> {
  try {
    return await apiCall();
  } catch (error) {
    // Error is already processed by our interceptor
    if (errorCallback && (error as ProcessedError).type) {
      errorCallback(error as ProcessedError);
    }
    throw error;
  }
}
