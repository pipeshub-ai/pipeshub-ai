/**
 * Runtime whitelabel configuration injected by Node.js backend
 * into index.html as window.__WHITELABEL_CONFIG__
 *
 * This enables changing whitelabel settings without rebuilding the Docker image.
 */
export interface RuntimeWhitelabelConfig {
  appName: string;
  appTitle: string;
  appTagline: string;
  githubUrl: string;
  docsBaseUrl: string;
  signinImageUrl: string;
  assistantName: string;
}

declare global {
  interface Window {
    __WHITELABEL_CONFIG__?: RuntimeWhitelabelConfig;
  }
}

export {};
