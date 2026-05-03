// ============================================================
// Authentication Settings — Types
// ============================================================

export type AuthMethodType =
  | 'password'
  | 'otp'
  | 'google'
  | 'microsoft'
  | 'samlSso'
  | 'oauth';

// ── API response shapes ──────────────────────────────────────

export interface ApiAuthMethod {
  type: AuthMethodType;
}

export interface ApiAuthStep {
  order: number;
  allowedMethods: ApiAuthMethod[];
}

/** Shape returned by GET /api/v1/orgAuthConfig/authMethods */
export interface GetAuthMethodsResponse {
  authMethods: ApiAuthStep[];
}

/** Shape sent to POST /api/v1/orgAuthConfig/updateAuthMethod */
export interface UpdateAuthMethodPayload {
  authMethod: ApiAuthStep[];
}

// ── Flat "enabled" state used in UI ─────────────────────────

export interface AuthMethodState {
  type: AuthMethodType;
  enabled: boolean;
}

// ── Provider config shapes ───────────────────────────────────

export interface GoogleConfig {
  clientId: string;
  enableJit: boolean;
}

export interface MicrosoftConfig {
  clientId: string;
  tenantId: string;
  enableJit: boolean;
}

export interface SamlConfig {
  entryPoint: string;
  certificate: string;
  emailKey: string;
  logoutUrl?: string;
  entityId?: string;
  enableJit: boolean;
  samlPlatform?: string;
}

export interface OAuthConfig {
  clientId: string;
  clientSecret: string;
  providerName: string;
  authorizationUrl: string;
  tokenEndpoint: string;
  userInfoEndpoint: string;
  scope?: string;
  redirectUri?: string;
  enableJit: boolean;
}

// ── Config status (which providers are fully configured) ─────

export interface ConfigStatus {
  google: boolean;
  microsoft: boolean;
  samlSso: boolean;
  oauth: boolean;
}

// ── Panel state ──────────────────────────────────────────────

/** Which method's configure panel is open */
export type ConfigurableMethod = Extract<
  AuthMethodType,
  'google' | 'microsoft' | 'samlSso' | 'oauth'
>;

export interface ConfigurePanelState {
  open: boolean;
  method: ConfigurableMethod | null;
}

// ── Per-method display metadata ──────────────────────────────

export interface AuthMethodMeta {
  type: AuthMethodType;
  label: string;
  description: string;
  /** Material icon name, or absolute public path for 'image' type */
  icon: string;
  /** Defaults to 'material'. Use 'image' for SVG files in /public */
  iconType?: 'material' | 'image';
  configurable: boolean;
  requiresSmtp: boolean;
}

export const AUTH_METHOD_META: AuthMethodMeta[] = [
  {
    type: 'password',
    label: 'Password',
    description: 'Traditional email and password authentication',
    icon: '/icons/auth-config/lock-closed.svg',
    iconType: 'image',
    configurable: false,
    requiresSmtp: false,
  },
  {
    type: 'otp',
    label: 'One-Time Password',
    description: 'Send a verification code via email',
    icon: 'mail_lock',
    configurable: false,
    requiresSmtp: true,
  },
  {
    type: 'google',
    label: 'Google',
    description: 'Allow users to sign in with Google accounts',
    icon: '/icons/auth-config/google.svg',
    iconType: 'image',
    configurable: true,
    requiresSmtp: false,
  },
  {
    type: 'microsoft',
    label: 'Microsoft',
    description: 'Allow users to sign in with Microsoft accounts',
    icon: '/icons/auth-config/windows.svg',
    iconType: 'image',
    configurable: true,
    requiresSmtp: false,
  },
  {
    type: 'samlSso',
    label: 'SAML SSO',
    description: 'Single Sign-On with SAML protocol',
    icon: 'security',
    configurable: true,
    requiresSmtp: false,
  },
  {
    type: 'oauth',
    label: 'OAuth',
    description: 'Generic OAuth 2.0 provider authentication',
    icon: 'vpn_key',
    configurable: true,
    requiresSmtp: false,
  },
];

/** All method types (used to build full toggleable list from API response) */
export const ALL_AUTH_METHOD_TYPES: AuthMethodType[] = [
  'password',
  'otp',
  'google',
  'microsoft',
  'samlSso',
  'oauth',
];
