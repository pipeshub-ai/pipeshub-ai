import { apiClient } from '@/lib/api';
import { publicAuthClient } from '@/lib/api/public-auth-client';
import type { SignInResponse } from '@/lib/api/auth-public-types';
import {
  getAuthSessionRequestConfig,
  postUserAccountAuthenticate,
} from '@/lib/api/post-user-account-authenticate';
import type {
  GetAuthMethodsResponse,
  UpdateAuthMethodPayload,
  GoogleConfig,
  MicrosoftConfig,
  SamlConfig,
  OAuthConfig,
} from './types';

// ============================================================
// Base URLs
// ============================================================

const AUTH_METHODS_URL = '/api/v1/orgAuthConfig';
const CONFIG_BASE = '/api/v1/configurationManager/authConfig';
const SMTP_URL = '/api/v1/configurationManager/smtpConfig';
const FRONTEND_URL = '/api/v1/configurationManager/frontendPublicUrl';

// ============================================================
// Auth Methods (enable/disable)
// ============================================================

export const AuthMethodsApi = {
  /**
   * GET /api/v1/orgAuthConfig/authMethods
   * Returns the current org auth steps & enabled methods.
   */
  async getAuthMethods(): Promise<GetAuthMethodsResponse> {
    const { data } = await apiClient.get<GetAuthMethodsResponse>(
      `${AUTH_METHODS_URL}/authMethods`,
    );
    return data;
  },

  /**
   * POST /api/v1/orgAuthConfig/updateAuthMethod
   * Saves updated enabled/disabled auth methods.
   */
  async updateAuthMethods(payload: UpdateAuthMethodPayload): Promise<void> {
    await apiClient.post(`${AUTH_METHODS_URL}/updateAuthMethod`, payload);
  },
};

// ============================================================
// Provider Configs
// ============================================================

export const AuthConfigApi = {
  // ── Google ────────────────────────────────────────────────

  /** GET /api/v1/configurationManager/authConfig/google */
  async getGoogleConfig(): Promise<GoogleConfig | null> {
    try {
      const { data } = await apiClient.get<GoogleConfig>(`${CONFIG_BASE}/google`);
      return data;
    } catch {
      return null;
    }
  },

  /** POST /api/v1/configurationManager/authConfig/google */
  async saveGoogleConfig(payload: GoogleConfig): Promise<void> {
    await apiClient.post(`${CONFIG_BASE}/google`, payload);
  },

  // ── Microsoft ─────────────────────────────────────────────

  /** GET /api/v1/configurationManager/authConfig/microsoft */
  async getMicrosoftConfig(): Promise<MicrosoftConfig | null> {
    try {
      const { data } = await apiClient.get<MicrosoftConfig>(`${CONFIG_BASE}/microsoft`);
      return data;
    } catch {
      return null;
    }
  },

  /** POST /api/v1/configurationManager/authConfig/microsoft */
  async saveMicrosoftConfig(payload: MicrosoftConfig): Promise<void> {
    await apiClient.post(`${CONFIG_BASE}/microsoft`, payload);
  },

  // ── SAML SSO ──────────────────────────────────────────────

  /** GET /api/v1/configurationManager/authConfig/sso */
  async getSamlConfig(): Promise<SamlConfig | null> {
    try {
      const { data } = await apiClient.get<SamlConfig>(`${CONFIG_BASE}/sso`);
      return data;
    } catch {
      return null;
    }
  },

  /** POST /api/v1/configurationManager/authConfig/sso */
  async saveSamlConfig(payload: SamlConfig): Promise<void> {
    await apiClient.post(`${CONFIG_BASE}/sso`, payload);
  },

  // ── OAuth ─────────────────────────────────────────────────

  /** GET /api/v1/configurationManager/authConfig/oauth */
  async getOAuthConfig(): Promise<OAuthConfig | null> {
    try {
      const { data } = await apiClient.get<OAuthConfig>(`${CONFIG_BASE}/oauth`);
      return data;
    } catch {
      return null;
    }
  },

  /** POST /api/v1/configurationManager/authConfig/oauth */
  async saveOAuthConfig(payload: OAuthConfig): Promise<void> {
    await apiClient.post(`${CONFIG_BASE}/oauth`, payload);
  },
};

// ============================================================
// SMTP (used to determine if OTP can be enabled)
// ============================================================

export interface SmtpStatus {
  configured: boolean;
}

export const SmtpApi = {
  /**
   * GET /api/v1/configurationManager/smtpConfig
   * Returns SMTP config. OTP is only enabled when host + fromEmail exist.
   */
  async checkSmtpConfigured(): Promise<boolean> {
    try {
      const { data } = await apiClient.get<{ host?: string; fromEmail?: string }>(SMTP_URL);
      return !!(data?.host && data?.fromEmail);
    } catch {
      return false;
    }
  },
};

// ============================================================
// Frontend URL (used to construct OAuth redirect URIs)
// ============================================================

export const FrontendUrlApi = {
  /**
   * GET /api/v1/configurationManager/frontendPublicUrl
   * Returns the configured frontend base URL.
   */
  async getFrontendUrl(): Promise<string> {
    try {
      const { data } = await apiClient.get<{ url: string }>(FRONTEND_URL);
      return data.url || window.location.origin;
    } catch {
      return window.location.origin;
    }
  },
};

// ============================================================
// User account login (unauthenticated — uses publicAuthClient, not apiClient)
// Used by sign-in page via AuthApi wrappers in app/(public)/api.ts
// ============================================================

export const UserAccountLoginApi = {
  /**
   * POST /api/v1/userAccount/login/otp/generate
   * Sends a one-time code to the given email. Optional `x-session-token` from initAuth.
   */
  async generateLoginOtp(email: string): Promise<void> {
    const sessionToken =
      typeof window !== 'undefined'
        ? sessionStorage.getItem('auth_session_token')
        : null;

    await publicAuthClient.post(
      '/api/v1/userAccount/login/otp/generate',
      { email },
      sessionToken
        ? { headers: { 'x-session-token': sessionToken } }
        : undefined,
    );
  },

  /**
   * POST /api/v1/userAccount/authenticate
   * Body: { email, method: 'otp', credentials: { otp } }
   */
  async signInWithOtp(email: string, otp: string): Promise<SignInResponse> {
    return postUserAccountAuthenticate(
      { email, method: 'otp', credentials: { otp } },
      getAuthSessionRequestConfig(),
    );
  },
};
