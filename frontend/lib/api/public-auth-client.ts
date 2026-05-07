import axios from 'axios';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { isElectron } from '@/lib/electron';

/**
 * Unauthenticated axios instance for login/sign-up flows (no Bearer interceptors).
 * Session correlation uses `x-session-token` from initAuth, stored in sessionStorage.
 */
export const publicAuthClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

// In Electron, override baseURL with the user-configured URL and disable
// withCredentials (auth uses Bearer tokens, not cookies).
publicAuthClient.interceptors.request.use((config) => {
  if (isElectron()) {
    config.baseURL = getApiBaseUrl();
    config.withCredentials = false;
  }
  return config;
});
