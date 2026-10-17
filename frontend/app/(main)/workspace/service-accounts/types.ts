/**
 * Service accounts and the tokens they hold.
 *
 * A service account is a machine identity: automation authenticates as one so
 * that it reads with its own permissions rather than borrowing a colleague's.
 * Nobody signs in as one, and it is always a member, never an administrator.
 */

/** GET /api/v1/service-accounts */
export interface ServiceAccount {
  id: string;
  slug: string;
  fullName: string;
  email: string;
  description?: string;
  isDisabled: boolean;
  createdAt?: string;
  updatedAt?: string;
}

export interface ServiceAccountListApiResponse {
  serviceAccounts: ServiceAccount[];
}

/** POST /api/v1/service-accounts */
export interface CreateServiceAccountPayload {
  slug: string;
  fullName: string;
  description?: string;
}

/** PATCH /api/v1/service-accounts/:id */
export interface UpdateServiceAccountPayload {
  fullName?: string;
  description?: string;
  isDisabled?: boolean;
}

/** GET /api/v1/service-tokens?serviceAccountId= */
export interface ServiceToken {
  id: string;
  name: string;
  serviceAccountId: string;
  scopes: string[];
  createdAt: string;
  expiresAt: string;
  lastUsedAt?: string;
}

export interface ServiceTokenListApiResponse {
  tokens: ServiceToken[];
}

/**
 * One permission a service token can be given, as the scope catalogue
 * describes it. The same shape the personal access token and OAuth
 * application pickers receive, so a permission reads the same wherever it is
 * offered.
 */
export interface ServiceTokenScopeItem {
  name: string;
  description: string;
  category: string;
  requiresUserConsent: boolean;
}

export interface ServiceTokenScopesApiResponse {
  scopes: ServiceTokenScopeItem[];
}

/** POST /api/v1/service-tokens */
export interface CreateServiceTokenPayload {
  serviceAccountId: string;
  name: string;
  /** Required and non-empty: these never default to every scope. */
  scopes: string[];
  expiryDays?: number;
}

/**
 * The raw token is in this response and nowhere else, ever. Only its hash is
 * stored, so there is no second chance to read it.
 */
export interface CreateServiceTokenApiResponse {
  message?: string;
  token: ServiceToken & { accessToken: string };
}

/**
 * One entry on a service account's allow-list: permission for it to act as
 * one named person for a few minutes at a time.
 *
 * Delegation is how an integration answers "what can *this person* see"
 * instead of reading with its own access. It is granted one person at a time
 * on purpose — an account allowed to act as anyone would be a master key.
 */
export interface Delegation {
  id: string;
  serviceAccountId: string;
  subjectUserId: string;
  subjectEmail?: string;
  subjectFullName?: string;
  /**
   * True when the person can no longer be acted as — they left, were
   * disabled, or became an administrator. The entry is still shown, because
   * hiding it would leave this list disagreeing with what the system does.
   */
  inactive: boolean;
  inactiveReason?: string;
  grantedBy: string;
  reason?: string;
  createdAt: string;
}

export interface DelegationListApiResponse {
  delegations: Delegation[];
}

/** POST /api/v1/service-account-delegations */
export interface GrantDelegationPayload {
  serviceAccountId: string;
  /** A user id or an email address. */
  subject: string;
  reason?: string;
}

/**
 * One thing that happened to this service account's delegation.
 *
 * Two kinds share the log. `issued` and `refused` are the exchange itself;
 * refusals are kept as deliberately as successes, because an account
 * repeatedly asking for people it may not act as is the clearest sign a
 * credential has been taken. `granted` and `revoked` are the allow-list, so
 * withdrawing a permission does not erase the record of who gave it.
 */
export interface DelegationEvent {
  id: string;
  serviceAccountId: string;
  serviceAccountEmail?: string;
  subjectUserId?: string;
  subjectEmail?: string;
  requestedSubject: string;
  outcome: 'issued' | 'refused' | 'granted' | 'revoked';
  refusedReason?: string;
  /** The administrator behind a `granted` or `revoked` entry. */
  actedBy?: string;
  actedByEmail?: string;
  /** The reason given when the permission was granted. */
  note?: string;
  scopes: string[];
  jti?: string;
  expiresAt?: string;
  createdAt: string;
}

export interface DelegationEventListApiResponse {
  events: DelegationEvent[];
}

/**
 * Whether this deployment can delegate at all.
 *
 * Letting a service account act as a named person is an Enterprise
 * capability. The Community build answers this probe with `available: false`
 * and refuses the rest of the delegation routes, so the screen asks first and
 * explains rather than failing when somebody reaches for it.
 */
export interface DelegationCapability {
  available: boolean;
  edition?: string;
  capability?: string;
}
