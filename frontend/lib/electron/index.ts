/**
 * Renderer-side Electron utilities. Anything Electron-specific (environment
 * detection, persisted-config helpers, request bridges) lives here so the rest
 * of the app keeps a single import path for "this only matters in Electron".
 */

export { isElectron } from './is-electron';
export {
  API_BASE_URL_STORAGE_KEY,
  setApiBaseUrl,
  hasStoredApiBaseUrl,
} from './api-base-url-storage';
export { streamingFetch } from './streaming-fetch';
