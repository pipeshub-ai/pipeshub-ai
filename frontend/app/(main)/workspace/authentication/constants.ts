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
  /** Provider-specific text in the description, e.g. "Google", "Microsoft" */
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
};
