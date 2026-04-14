import axios from 'axios';
import { getApiBaseUrl, isElectron } from '@/lib/utils/api-base-url';

/**
 * Unauthenticated axios instance for login/sign-up flows (no Bearer interceptors).
 * Session correlation uses `x-session-token` from initAuth, stored in sessionStorage.
 */
export const publicAuthClient = axios.create({
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Dynamically set baseURL on each request so the Electron-configured URL is picked up.
// Disable withCredentials in Electron — auth uses Bearer tokens, not cookies.
publicAuthClient.interceptors.request.use((config) => {
  config.baseURL = getApiBaseUrl();
  if (isElectron()) {
    config.withCredentials = false;
  }
  return config;
});
