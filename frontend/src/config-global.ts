import { paths } from 'src/routes/paths';

import packageJson from '../package.json';

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
};

// ----------------------------------------------------------------------

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
};
