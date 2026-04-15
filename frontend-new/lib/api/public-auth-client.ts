import axios from 'axios';
import { getApiBaseUrl } from './base-url';

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

// Resolve base URL at request time so it picks up runtime configuration
publicAuthClient.interceptors.request.use((config) => {
  config.baseURL = getApiBaseUrl();
  return config;
});
