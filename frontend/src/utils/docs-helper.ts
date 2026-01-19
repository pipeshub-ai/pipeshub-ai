import { getDocsUrl, hasDocsLink } from 'src/config-global';

/**
 * Get full documentation URL for a specific path
 * Returns empty string if docs are not configured
 */
export const getDocumentationUrl = (path: string): string => getDocsUrl(path);

/**
 * Check if documentation links should be shown
 */
export const shouldShowDocs = (): boolean => hasDocsLink();

/**
 * Default paths for common documentation sections
 */
export const DOCS_PATHS = {
  CONNECTORS_OVERVIEW: 'connectors/overview',
  AI_MODELS_OVERVIEW: 'ai-models/overview',
  STORAGE: 'system-overview/storage',
  SMTP: 'smtp',
  SAML: 'authentication/saml',
  GOOGLE_AUTH: 'authentication/google',
  MICROSOFT_AUTH: 'authentication/microsoft',
  AZURE_AD: 'authentication/azure-ad',
} as const;
