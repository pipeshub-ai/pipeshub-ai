// ============================================================
// Authentication — Provider Form Config
//
// Each configurable provider defines:
//  • fields   — declarative field schema (drives the unified form renderer)
//  • loadValues — async fn that fetches existing config and returns
//                 { key: string | boolean } form state.
//                 Keys prefixed with `_` are internal meta (e.g. `_uriMismatch`);
//                 they are used by field definitions but never sent to the API.
//  • saveValues — async fn that maps form state back to the API call.
// ============================================================

import { AuthConfigApi, FrontendUrlApi } from './api';
import type { ConfigurableMethod } from './types';

// ── Field definition types ───────────────────────────────────

interface BaseFieldDef {
  key: string;
  label: string;
  /** Shown after the label in muted weight, e.g. "(optional)" */
  labelSuffix?: string;
  helperText?: string;
}

export interface TextFieldDef extends BaseFieldDef {
  type: 'text';
  placeholder?: string;
  required?: boolean;
  /** Material icon name shown as a leading slot */
  icon?: string;
}

export interface PasswordFieldDef extends BaseFieldDef {
  type: 'password';
  placeholder?: string;
  required?: boolean;
}

export interface TextareaFieldDef extends BaseFieldDef {
  type: 'textarea';
  placeholder?: string;
  required?: boolean;
  monospace?: boolean;
  minHeight?: number;
}

export interface ReadonlyFieldDef extends BaseFieldDef {
  type: 'readonly';
  /**
   * If this key exists in the form values and is `true`, a warning Callout
   * is rendered below the input using `warningText`.
   */
  warningKey?: string;
  warningText?: string;
}

/** The standard JIT-provisioning toggle; always uses key "enableJit" */
export interface JitFieldDef {
  type: 'jit';
  key: 'enableJit';
  /** Provider-specific text in the description, e.g. "Google", "SAML" */
  providerName: string;
}

export type FieldDef =
  | TextFieldDef
  | PasswordFieldDef
  | TextareaFieldDef
  | ReadonlyFieldDef
  | JitFieldDef;

// ── Provider config type ─────────────────────────────────────

export interface ProviderFormConfig {
  fields: FieldDef[];
  /**
   * Fetch existing saved config and return initial form values.
   * Boolean values map to toggle fields; all others are strings.
   * `_*` keys are internal meta and won't be sent to the API.
   */
  loadValues: () => Promise<Record<string, string | boolean>>;
  /** Map form values back to the API save call. */
  saveValues: (values: Record<string, string | boolean>) => Promise<void>;
}

// ── Helper ───────────────────────────────────────────────────

const getBaseUrl = async (): Promise<string> => {
  const url = await FrontendUrlApi.getFrontendUrl();
  return (url || window.location.origin).replace(/\/$/, '');
};

// ── Provider configs ──────────────────────────────────────────

export const PROVIDER_CONFIGS: Record<ConfigurableMethod, ProviderFormConfig> = {
  // ── Google ────────────────────────────────────────────────
  google: {
    fields: [
      {
        type: 'readonly',
        key: 'redirectUri',
        label: 'Redirect URI',
        labelSuffix: '(add to your Google OAuth settings)',
      },
      {
        type: 'readonly',
        key: 'authorizedOrigin',
        label: 'Authorized Origin',
        labelSuffix: '(add to your Google OAuth settings)',
        warningKey: '_uriMismatch',
        warningText:
          'The current origin differs from the configured frontend URL. Use the value above in Google OAuth settings.',
      },
      {
        type: 'text',
        key: 'clientId',
        label: 'Client ID',
        placeholder: 'Enter your Google OAuth Client ID',
        required: true,
        icon: 'tag',
        helperText: 'The client ID from your Google OAuth credentials',
      },
      { type: 'jit', key: 'enableJit', providerName: 'Google' },
    ],

    async loadValues() {
      const [config, frontendUrl] = await Promise.all([
        AuthConfigApi.getGoogleConfig(),
        FrontendUrlApi.getFrontendUrl(),
      ]);
      const currentRedirect = `${window.location.origin}/auth/google/callback`;
      const recommendedRedirect = frontendUrl
        ? `${frontendUrl.replace(/\/$/, '')}/auth/google/callback`
        : currentRedirect;
      return {
        redirectUri: recommendedRedirect,
        authorizedOrigin: frontendUrl || window.location.origin,
        _uriMismatch: currentRedirect !== recommendedRedirect,
        clientId: config?.clientId ?? '',
        enableJit: config?.enableJit ?? true,
      };
    },

    async saveValues(values) {
      await AuthConfigApi.saveGoogleConfig({
        clientId: String(values.clientId).trim(),
        enableJit: Boolean(values.enableJit),
      });
    },
  },

  // ── Microsoft ─────────────────────────────────────────────
  microsoft: {
    fields: [
      {
        type: 'readonly',
        key: 'redirectUri',
        label: 'Redirect URI',
        labelSuffix: '(add to your Microsoft OAuth settings)',
      },
      {
        type: 'text',
        key: 'clientId',
        label: 'Client ID',
        placeholder: 'Enter your Microsoft Application (client) ID',
        required: true,
        icon: 'tag',
        helperText: 'The Application (client) ID from your Azure portal app registration',
      },
      {
        type: 'text',
        key: 'tenantId',
        label: 'Tenant ID',
        placeholder: 'Enter your Microsoft Directory (tenant) ID',
        required: true,
        icon: 'tag',
        helperText: 'The Directory (tenant) ID from your Azure portal app registration',
      },
      { type: 'jit', key: 'enableJit', providerName: 'Microsoft' },
    ],

    async loadValues() {
      const [config, baseUrl] = await Promise.all([
        AuthConfigApi.getMicrosoftConfig(),
        getBaseUrl(),
      ]);
      return {
        redirectUri: `${baseUrl}/auth/microsoft/callback`, 
        clientId: config?.clientId ?? '',
        tenantId: config?.tenantId ?? '',
        enableJit: config?.enableJit ?? true,
      };
    },

    async saveValues(values) {
      await AuthConfigApi.saveMicrosoftConfig({
        clientId: String(values.clientId).trim(),
        tenantId: String(values.tenantId).trim(),
        enableJit: Boolean(values.enableJit),
      });
    },
  },

  // ── SAML SSO ──────────────────────────────────────────────
  samlSso: {
    fields: [
      {
        type: 'text',
        key: 'entryPoint',
        label: 'SSO Entry Point (IdP URL)',
        placeholder: 'https://your-idp.example.com/sso/saml',
        required: true,
        helperText: 'The Single Sign-On URL provided by your Identity Provider',
      },
      {
        type: 'textarea',
        key: 'certificate',
        label: 'X.509 Certificate',
        placeholder: '-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----',
        required: true,
        monospace: true,
        minHeight: 100,
        helperText: 'The public certificate from your Identity Provider (PEM format)',
      },
      {
        type: 'text',
        key: 'emailKey',
        label: 'Email Attribute Key',
        placeholder:
          'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
        required: true,
        helperText: "The SAML attribute name that contains the user's email address",
      },
      {
        type: 'text',
        key: 'entityId',
        label: 'Entity ID',
        labelSuffix: '(optional)',
        placeholder: 'https://your-app.example.com',
        helperText: 'The service provider entity ID (defaults to your app URL if empty)',
      },
      {
        type: 'text',
        key: 'logoutUrl',
        label: 'Logout URL',
        labelSuffix: '(optional)',
        placeholder: 'https://your-idp.example.com/logout',
      },
      {
        type: 'text',
        key: 'samlPlatform',
        label: 'Provider Name',
        labelSuffix: '(optional)',
        placeholder: 'e.g. OKTA, Azure AD',
      },
      { type: 'jit', key: 'enableJit', providerName: 'SAML' },
    ],

    async loadValues() {
      const config = await AuthConfigApi.getSamlConfig();
      return {
        entryPoint: config?.entryPoint ?? '',
        certificate: config?.certificate ?? '',
        emailKey:
          config?.emailKey ??
          'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
        entityId: config?.entityId ?? '',
        logoutUrl: config?.logoutUrl ?? '',
        samlPlatform: config?.samlPlatform ?? '',
        enableJit: config?.enableJit ?? true,
      };
    },

    async saveValues(values) {
      await AuthConfigApi.saveSamlConfig({
        entryPoint: String(values.entryPoint).trim(),
        certificate: String(values.certificate).trim(),
        emailKey: String(values.emailKey).trim(),
        samlPlatform: String(values.samlPlatform).trim() || undefined,
        logoutUrl: String(values.logoutUrl).trim() || undefined,
        entityId: String(values.entityId).trim() || undefined,
        enableJit: Boolean(values.enableJit),
      });
    },
  },

  // ── OAuth 2.0 ─────────────────────────────────────────────
  oauth: {
    fields: [
      {
        type: 'text',
        key: 'providerName',
        label: 'Provider Name',
        placeholder: 'e.g. Okta, Auth0, Keycloak',
        required: true,
      },
      {
        type: 'readonly',
        key: 'redirectUri',
        label: 'Redirect URI',
        labelSuffix: '(register in your OAuth provider)',
      },
      {
        type: 'text',
        key: 'clientId',
        label: 'Client ID',
        placeholder: 'Enter your OAuth Client ID',
        required: true,
        icon: 'tag',
      },
      {
        type: 'password',
        key: 'clientSecret',
        label: 'Client Secret',
        placeholder: 'Enter your OAuth Client Secret',
        required: true,
      },
      {
        type: 'text',
        key: 'authorizationUrl',
        label: 'Authorization URL',
        placeholder: 'https://provider.example.com/oauth/authorize',
        required: true,
      },
      {
        type: 'text',
        key: 'tokenEndpoint',
        label: 'Token Endpoint',
        placeholder: 'https://provider.example.com/oauth/token',
        required: true,
      },
      {
        type: 'text',
        key: 'userInfoEndpoint',
        label: 'UserInfo Endpoint',
        placeholder: 'https://provider.example.com/userinfo',
        required: true,
      },
      {
        type: 'text',
        key: 'scope',
        label: 'Scope',
        labelSuffix: '(optional)',
        placeholder: 'openid email profile',
        helperText: 'Space-separated OAuth scopes. Defaults to openid email profile',
      },
      { type: 'jit', key: 'enableJit', providerName: 'OAuth' },
    ],

    async loadValues() {
      const [config, baseUrl] = await Promise.all([
        AuthConfigApi.getOAuthConfig(),
        getBaseUrl(),
      ]);
      const defaultRedirect = `${baseUrl}/auth/oauth/callback`;
      return {
        providerName: config?.providerName ?? '',
        redirectUri: config?.redirectUri ?? defaultRedirect,
        clientId: config?.clientId ?? '',
        clientSecret: config?.clientSecret ?? '',
        authorizationUrl: config?.authorizationUrl ?? '',
        tokenEndpoint: config?.tokenEndpoint ?? '',
        userInfoEndpoint: config?.userInfoEndpoint ?? '',
        scope: config?.scope ?? 'openid email profile',
        enableJit: config?.enableJit ?? true,
      };
    },

    async saveValues(values) {
      await AuthConfigApi.saveOAuthConfig({
        providerName: String(values.providerName).trim(),
        clientId: String(values.clientId).trim(),
        clientSecret: String(values.clientSecret).trim(),
        authorizationUrl: String(values.authorizationUrl).trim(),
        tokenEndpoint: String(values.tokenEndpoint).trim(),
        userInfoEndpoint: String(values.userInfoEndpoint).trim(),
        scope: String(values.scope).trim() || 'openid email profile',
        redirectUri: String(values.redirectUri).trim() || undefined,
        enableJit: Boolean(values.enableJit),
      });
    },
  },
};
