import { paths } from 'src/routes/paths';

import packageJson from '../package.json';

import type { RuntimeWhitelabelConfig } from 'src/types/runtime-config';

// ----------------------------------------------------------------------

export type ConfigValue = {
  appName: string;
  appVersion: string;
  assetsDir: string;
  backendUrl: string;
  notificationBackendUrl: string;
  authUrl: string;
  iamUrl: string;
  auth: {
    method: 'jwt';
    skip: boolean;
    redirectPath: string;
  };
  aiBackend: string;
  turnstileSiteKey: string;
  whitelabel: {
    appName: string;
    appTitle: string;
    appTagline: string;
    githubUrl: string;
    docsBaseUrl: string;
    signinImageUrl: string;
    assistantName: string;
  };
};

// ----------------------------------------------------------------------

// Runtime config takes precedence over build-time config (for Docker runtime injection)
const getRuntimeConfig = (): RuntimeWhitelabelConfig | undefined => {
  if (typeof window !== 'undefined' && window.__WHITELABEL_CONFIG__) {
    return window.__WHITELABEL_CONFIG__;
  }
  return undefined;
};

const runtimeConfig = getRuntimeConfig();

export const CONFIG: ConfigValue = {
  appName: 'PipesHub',
  appVersion: packageJson.version,
  backendUrl: import.meta.env.VITE_BACKEND_URL ?? '',
  notificationBackendUrl: import.meta.env.VITE_NOTIFICATION_BACKEND_URL ?? '',
  authUrl: import.meta.env.VITE_AUTH_URL ?? '',
  assetsDir: import.meta.env.VITE_ASSETS_DIR ?? '',
  iamUrl: import.meta.env.VITE_IAM_URL ?? '',
  aiBackend: import.meta.env.VITE_AI_BACKEND ?? '',
  turnstileSiteKey: import.meta.env.VITE_TURNSTILE_SITE_KEY ?? '',
  /**
   * Auth
   * @method jwt
   */
  auth: {
    method: 'jwt',
    skip: false,
    redirectPath: paths.dashboard.root,
  },
  whitelabel: {
    appName: runtimeConfig?.appName ?? import.meta.env.VITE_APP_NAME ?? '',
    appTitle: runtimeConfig?.appTitle ?? import.meta.env.VITE_APP_TITLE ?? '',
    appTagline: runtimeConfig?.appTagline ?? import.meta.env.VITE_APP_TAGLINE ?? '',
    githubUrl: runtimeConfig?.githubUrl ?? import.meta.env.VITE_GITHUB_URL ?? '',
    docsBaseUrl: runtimeConfig?.docsBaseUrl ?? import.meta.env.VITE_DOCS_BASE_URL ?? '',
    signinImageUrl: runtimeConfig?.signinImageUrl ?? import.meta.env.VITE_SIGNIN_IMAGE_URL ?? '',
    assistantName: runtimeConfig?.assistantName ?? import.meta.env.VITE_ASSISTANT_NAME ?? '',
  },
};

// Whitelabel helper functions
export const getAppName = () => CONFIG.whitelabel.appName || '';
export const getAppTitle = () => CONFIG.whitelabel.appTitle || '';
export const getAppTagline = () => CONFIG.whitelabel.appTagline || '';
export const getGithubUrl = () => CONFIG.whitelabel.githubUrl;
export const getDocsUrl = (path: string = '') => {
  const base = CONFIG.whitelabel.docsBaseUrl;
  if (!base) return '';
  // Handle trailing slashes
  const cleanBase = base.replace(/\/$/, '');
  const cleanPath = path.replace(/^\//, '');
  return cleanPath ? `${cleanBase}/${cleanPath}` : cleanBase;
};
export const hasGithubLink = () => Boolean(CONFIG.whitelabel.githubUrl);
export const hasDocsLink = () => Boolean(CONFIG.whitelabel.docsBaseUrl);
export const getSigninImageUrl = () => CONFIG.whitelabel.signinImageUrl;
export const hasSigninImage = () => Boolean(CONFIG.whitelabel.signinImageUrl);
export const getAssistantName = () => CONFIG.whitelabel.assistantName || 'Assistant';
