import type { InternalAxiosRequestConfig } from 'axios';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { isElectron } from './is-electron';

/**
 * Inside Electron the renderer is loaded from `app://` and must talk to
 * whichever API URL the user configured at first launch. Cookie-based auth
 * doesn't work cross-origin from `app://`, so we also disable
 * `withCredentials` and rely on Bearer tokens. Every axios instance that
 * could fire from Electron should run this in its request interceptor.
 */
export function applyElectronOverrides(
  config: InternalAxiosRequestConfig,
): InternalAxiosRequestConfig {
  if (isElectron()) {
    config.baseURL = getApiBaseUrl();
    config.withCredentials = false;
  }
  return config;
}
