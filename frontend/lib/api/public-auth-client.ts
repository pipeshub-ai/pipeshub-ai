import axios from 'axios';

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
