// ============================================================
// Authentication Settings — Types
// ============================================================

export type AuthMethodType = 'password' | 'otp' | 'google' | 'microsoft';

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

// ── Config status (which providers are fully configured) ─────

export interface ConfigStatus {
  google: boolean;
  microsoft: boolean;
}

// ── Sign-in methods removed in 1.0 ───────────────────────────

/** Sign-in methods the 1.0 upgrade turned off. */
export type RemovedSignInMethod = 'samlSso' | 'oauth';

export interface PersonWithoutPassword {
  userId: string;
  fullName?: string;
  email: string;
}

/** Shape returned by GET /api/v1/orgAuthConfig/signInRemoval */
export interface SignInRemovalStatus {
  removedMethods: RemovedSignInMethod[];
  peopleWithoutPassword: PersonWithoutPassword[];
}

// ── Panel state ──────────────────────────────────────────────

/** Which method's configure panel is open */
export type ConfigurableMethod = Extract<AuthMethodType, 'google' | 'microsoft'>;

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
];

/** All method types (used to build full toggleable list from API response) */
export const ALL_AUTH_METHOD_TYPES: AuthMethodType[] = ['password', 'otp', 'google', 'microsoft'];
