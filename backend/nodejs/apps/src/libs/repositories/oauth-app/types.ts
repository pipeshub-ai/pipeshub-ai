import type { KnowledgeLimit } from '../../../modules/oauth_provider/types/oauth.types';

export enum OAuthAppStatus {
  ACTIVE = 'active',
  SUSPENDED = 'suspended',
  REVOKED = 'revoked',
}

export enum OAuthGrantType {
  AUTHORIZATION_CODE = 'authorization_code',
  CLIENT_CREDENTIALS = 'client_credentials',
  REFRESH_TOKEN = 'refresh_token',
  DEVICE_CODE = 'urn:ietf:params:oauth:grant-type:device_code',
  /**
   * RFC 8693 token exchange, used only by service-account delegation.
   *
   * Never added to an app's `allowedGrantTypes`: the exchange authenticates
   * with the service token itself rather than with client credentials, so
   * there is no registered client whose grant list could permit it.
   */
  TOKEN_EXCHANGE = 'urn:ietf:params:oauth:grant-type:token-exchange',
}

/**
 * `createdBy` of an app no person created: a client that registered itself (RFC 7591) and the
 * first-party device app. A valid id in both stores that no user has, so such an app is in
 * nobody's list and its tokens never act as a person (the token service and auth middleware).
 */
export const NO_APP_CREATOR = '000000000000000000000000';

/**
 * `ownerFormat` of every app this code stores: its owner fields follow the rules above (no borrowed
 * creator). An app without it was stored by an older release, which is what the one-time repair
 * (`disownInstanceApps`) looks for; the repair sets it on what it fixes.
 */
export const APP_OWNER_FORMAT = 1;

/** A stored OAuth app with every id as a string; same keys as the stored row. */
export interface OAuthAppDto {
  _id: string;
  slug: string;
  clientId: string;
  clientSecretEncrypted: string;
  name: string;
  description?: string;
  orgId: string;
  createdBy: string;
  /** Identity the app's `client_credentials` tokens act as; absent means `createdBy`. */
  tokenIdentityUserId?: string;
  redirectUris: string[];
  allowedGrantTypes: OAuthGrantType[];
  allowedScopes: string[];
  status: OAuthAppStatus;
  logoUrl?: string;
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  isConfidential: boolean;
  isDynamic?: boolean;
  /** `APP_OWNER_FORMAT` on an app stored or repaired by this code; absent on one an older release stored. */
  ownerFormat?: number;
  /** What every token of this app may reach; absent means everything the connecting user can see. */
  knowledgeLimit?: KnowledgeLimit;
  accessTokenLifetime: number;
  refreshTokenLifetime: number;
  isDeleted: boolean;
  deletedBy?: string;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

export interface NewOAuthApp {
  clientId: string;
  clientSecretEncrypted: string;
  name: string;
  description?: string;
  orgId: string;
  createdBy: string;
  /** The service account the app's `client_credentials` tokens act as, when named. */
  tokenIdentityUserId?: string;
  redirectUris: string[];
  allowedGrantTypes: OAuthGrantType[];
  allowedScopes: string[];
  homepageUrl?: string;
  privacyPolicyUrl?: string;
  termsOfServiceUrl?: string;
  logoUrl?: string;
  isConfidential: boolean;
  isDynamic?: boolean;
  knowledgeLimit?: KnowledgeLimit;
  status?: OAuthAppStatus;
  accessTokenLifetime?: number;
  refreshTokenLifetime?: number;
}

/**
 * Fields an update may set. A key that is present with `undefined` clears the
 * field; `tokenIdentityUserId` and `deletedBy` are user ids as strings.
 */
export type OAuthAppPatch = Partial<
  Pick<
    OAuthAppDto,
    | 'name'
    | 'description'
    | 'redirectUris'
    | 'allowedGrantTypes'
    | 'allowedScopes'
    | 'homepageUrl'
    | 'privacyPolicyUrl'
    | 'termsOfServiceUrl'
    | 'accessTokenLifetime'
    | 'refreshTokenLifetime'
    | 'knowledgeLimit'
    | 'clientSecretEncrypted'
    | 'status'
    | 'isDeleted'
    | 'deletedBy'
    | 'tokenIdentityUserId'
  >
>;

export interface OwnedAppListQuery {
  status?: string;
  search?: string;
  skip: number;
  limit: number;
}

/** An administrator's view: every app of the org, optionally only one person's. */
export interface OrgAppListQuery extends OwnedAppListQuery {
  createdBy?: string;
}

/**
 * "Owned" means: in the org, created by the user, not deleted, and not one of the
 * internal clients (the per-org personal-token and service-token apps, the
 * first-party device app, dynamically registered clients).
 *
 * `client_id` to org for a caller that does not know the org yet goes through
 * `IdentityLookup.resolveClient`, not through this repository.
 */
export interface UpdateOwnedOptions {
  /**
   * Save only over this stored knowledge limit (`undefined` or `null` for none): when the stored limit has changed
   * since the caller read it, nothing is saved and a ConflictError is thrown.
   */
  overKnowledgeLimit?: KnowledgeLimit | null;
  /** With `overKnowledgeLimit`: a stale save is skipped silently (a rollback must not overwrite a later edit). */
  ignoreStale?: boolean;
}

export interface OAuthAppRepository {
  create(input: NewOAuthApp): Promise<OAuthAppDto>;
  findOwned(
    orgId: string,
    userId: string,
    appId: string,
  ): Promise<OAuthAppDto | null>;
  listOwned(
    orgId: string,
    userId: string,
    q: OwnedAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }>;
  /**
   * Loads the owned app, applies the patch and saves it, so array replacement
   * stays version-guarded as it was with a document save. Null when the app is
   * not owned or does not exist.
   */
  updateOwned(
    orgId: string,
    userId: string,
    appId: string,
    patch: OAuthAppPatch,
    opts?: UpdateOwnedOptions,
  ): Promise<OAuthAppDto | null>;
  /** Same filter as `findOwned` without the creator: any app an administrator of the org manages. */
  findInOrg(orgId: string, appId: string): Promise<OAuthAppDto | null>;
  /** Same filter and order as `listOwned` without the creator. */
  listInOrg(
    orgId: string,
    q: OrgAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }>;
  /** Not deleted. Used by the token endpoints, after the client is known. */
  findLiveByClientId(clientId: string): Promise<OAuthAppDto | null>;
  /** The scopes and knowledge limit a live app allows now, or null when the app is gone or deleted. */
  findLiveAllowedScopes(clientId: string): Promise<{
    allowedScopes: string[];
    knowledgeLimit?: KnowledgeLimit;
  } | null>;
  /** Who a live app's `client_credentials` tokens act as: `tokenIdentityUserId` when set, else `createdBy`. Read per request. */
  findLiveIdentity(
    clientId: string,
  ): Promise<{ createdBy: string; tokenIdentityUserId?: string } | null>;
  /** Including deleted: internal clients (personal tokens, service tokens, device login) and token exchange. */
  findByClientId(clientId: string): Promise<OAuthAppDto | null>;
  /** Adds scopes to the allowed list of the app with this id (never removes one). */
  addAllowedScopes(id: string, scopes: string[]): Promise<void>;
  /**
   * Replaces the scopes and grant types of a live dynamically registered app. Dynamic apps
   * have no owner who edits them, so their grant follows the client's metadata and the
   * instance's limits instead.
   */
  setDynamicGrant(
    orgId: string,
    id: string,
    grant: Pick<OAuthAppDto, 'allowedScopes' | 'allowedGrantTypes'>,
  ): Promise<void>;
  /**
   * Repair of the instance apps of `orgId` (dynamically registered clients and the first-party
   * device app, deleted or not) that an older release stored, with a borrowed user as their
   * creator: every such app without `ownerFormat`. Sets `createdBy` to `NO_APP_CREATOR` and
   * `ownerFormat`, and stores each dynamic client under `dynamicClientOrgId` when that is another
   * org. An app this code stored (a metadata client keeps the person who consented) has
   * `ownerFormat` and is left alone, so it is safe to run at every start. Returns how many apps
   * changed, each counted once. PostgreSQL keeps every row in its org, so there a different org
   * is refused.
   */
  disownInstanceApps(
    orgId: string,
    dynamicClientOrgId: string,
  ): Promise<number>;
  /** Client ids of live apps whose tokens act as the given user (a service account). */
  listClientIdsActingAs(userId: string): Promise<string[]>;
  /**
   * Client ids of live apps whose own `client_credentials` tokens act as the user because
   * they created the app and it names no service account.
   */
  listClientIdsActingAsCreator(
    orgId: string,
    userId: string,
  ): Promise<string[]>;
  /** Soft-deletes and revokes the apps `listClientIdsActingAsCreator` lists. */
  softDeleteActingAsCreator(
    orgId: string,
    userId: string,
    deletedBy: string,
  ): Promise<void>;
  /** Every app of the org with its stored secret, deleted ones too (the `SECRET_KEY` rotation's walk, ADR-KEY-01). */
  listClientSecrets(
    orgId: string,
  ): Promise<Array<{ id: string; clientSecretEncrypted: string }>>;
  /** Replaces the stored secret only while it is still `expected`; false when it changed since or the app is gone. */
  replaceClientSecret(
    orgId: string,
    id: string,
    expected: string,
    next: string,
  ): Promise<boolean>;
  /** Makes `toUserId` the creator of every live app `fromUserId` created, internal ones included. */
  transferCreatedBy(
    orgId: string,
    fromUserId: string,
    toUserId: string,
  ): Promise<void>;
}
