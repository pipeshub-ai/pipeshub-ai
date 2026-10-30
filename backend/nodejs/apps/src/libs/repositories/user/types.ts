/**
 * User repository: the people (and service accounts) of an organisation.
 *
 * Rows leave as plain objects with every id as a string; dates stay `Date` so a
 * response built from them serialises as the document did. No driver type
 * appears here. Methods are use cases, not filters: "live" means not removed,
 * and who may see what is the caller's decision.
 *
 * Every method that reads or writes inside one organisation takes its id; the
 * only cross-organisation calls are on `IdentityPort`.
 */
import type { Tx } from '../uow';

export type UserRole = 'admin' | 'member';

export interface UserDto {
  _id: string;
  orgId: string;
  slug?: string;
  fullName?: string;
  firstName?: string;
  lastName?: string;
  middleName?: string;
  email: string;
  mobile?: string;
  hasLoggedIn?: boolean;
  designation?: string;
  kind?: 'human' | 'service';
  description?: string;
  isDisabled?: boolean;
  restoreOpId?: string;
  role?: UserRole;
  address?: Record<string, unknown>;
  isDeleted?: boolean;
  deletedBy?: string;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

/** Fields a caller may write on an account; anything else is ignored by the store. */
export type UserFields = Partial<
  Pick<
    UserDto,
    | 'fullName'
    | 'firstName'
    | 'lastName'
    | 'middleName'
    | 'email'
    | 'mobile'
    | 'hasLoggedIn'
    | 'designation'
    | 'role'
    | 'address'
    | 'kind'
    | 'description'
    | 'isDisabled'
    | 'isDeleted'
  >
> &
  Record<string, unknown>;

export interface DirectoryQuery {
  skip: number;
  limit: number;
  search?: string;
  /** Service accounts are left out unless the caller may see them. */
  includeServiceAccounts: boolean;
  hasLoggedIn?: boolean;
  /** `true`: only blocked people (or, with `hasLoggedIn`, either); `false`: only the unblocked. */
  blocked?: boolean;
  groupIds?: string[];
  /** Joined in `[from, to)`; either bound may be left out. */
  createdAt?: TimeRange;
  /** Last updated (shown as last active) in `[from, to)`. */
  updatedAt?: TimeRange;
}

export interface TimeRange {
  from?: Date;
  to?: Date;
}

export interface UserWithGroupsDto {
  _id: string;
  userId?: string;
  orgId: string;
  fullName?: string;
  hasLoggedIn?: boolean;
  groups: Array<{ name: string; type: string }>;
}

export interface UserAccountState {
  kind?: 'human' | 'service';
  isDisabled?: boolean;
}

export interface UserAuthSnapshot {
  _id: string;
  email: string;
  fullName?: string;
  role?: UserRole;
  isDisabled?: boolean;
  kind?: 'human' | 'service';
}

export interface UserRoleSnapshot {
  role?: UserRole | null;
  kind?: string | null;
}

export interface MemberSummaryDto {
  _id: string;
  fullName?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}

export interface RestoreServiceAccountInput {
  fullName: string;
  /** `undefined` clears the stored text. */
  description?: string;
  restoreOpId: string;
  role: UserRole;
}

export interface UserRepository {
  // --- single account
  /** The live account in this organisation. */
  getLive(orgId: string, id: string): Promise<UserDto | null>;
  /** The live account with stored defaults applied, for a caller about to change it. */
  getForEdit(orgId: string, id: string): Promise<UserDto | null>;
  /** Just the address of a live account. */
  getEmail(orgId: string, id: string): Promise<Pick<UserDto, 'email'> | null>;
  /** The account in this organisation whether or not it was removed. */
  getAny(orgId: string, id: string): Promise<UserDto | null>;
  /** The live account by id alone (session check: the token names the organisation). */
  getLiveById(id: string): Promise<UserDto | null>;
  /** Whether the account may hold a session at all. */
  getAccountState(id: string): Promise<UserAccountState | null>;
  /** The few fields a token-authenticated request needs. */
  getAuthSnapshot(orgId: string, id: string): Promise<UserAuthSnapshot | null>;
  /** Role and kind of a not-removed account. */
  getRoleSnapshot(orgId: string, id: string): Promise<UserRoleSnapshot | null>;
  /**
   * Only the named fields of a live account in this organisation (token and
   * consent checks that need a few columns). `serviceOnly` also requires a
   * service account.
   */
  getLiveFields<K extends keyof UserDto>(
    orgId: string,
    id: string,
    fields: K[],
    opts?: { serviceOnly?: boolean; strict?: boolean },
  ): Promise<Pick<UserDto, K> | null>;
  /** The account in this organisation by id or address, removed ones included (delegation checks). */
  getByIdOrEmail<K extends keyof UserDto>(
    orgId: string,
    key: { id: string } | { email: string },
    fields: K[],
  ): Promise<Pick<UserDto, K> | null>;
  /** Who may sign in to the organisation: signed in before, not disabled, not a service account. `_id`, name and address only. */
  listSignInEligible(
    orgId: string,
  ): Promise<Array<Pick<UserDto, '_id' | 'fullName' | 'email'>>>;
  /** The live accounts at this address (in one organisation when `orgId` is given), only the named fields. */
  listLiveByEmail<K extends keyof UserDto>(
    email: string,
    fields: K[],
    orgId?: string,
  ): Promise<Array<Pick<UserDto, K>>>;
  /** An account at this address that is not removed (a row without the flag counts), as `{ _id, orgId, email }`. */
  findActiveByEmail(
    email: string,
  ): Promise<{ _id: string; orgId: string; email: string } | null>;
  /** The live accounts at this address, as `{ _id, orgId }`. */
  listLiveMembershipsByEmail(
    email: string,
  ): Promise<Array<{ _id: string; orgId: string }>>;
  /** Only the named fields of the live accounts with these ids, in any organisation. */
  listLiveFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>>;
  /** Only the named fields of the accounts with these ids, in any organisation and any state. */
  listFieldsByIds<K extends keyof UserDto>(
    ids: string[],
    fields: K[],
  ): Promise<Array<Pick<UserDto, K>>>;
  /** The id of the organisation's longest-standing not-removed account. */
  getOldestId(orgId: string): Promise<string | null>;
  /** The account whether or not it was removed, by id alone. */
  getAnyById(id: string): Promise<UserDto | null>;
  /** The live account in this organisation at this address. */
  getLiveByEmail(orgId: string, email: string): Promise<UserDto | null>;
  /** Name and address of an account, in any organisation (the caller is signed in as it). */
  getContact(
    id: string,
  ): Promise<Pick<UserDto, '_id' | 'fullName' | 'email'> | null>;
  /** An account at this address in any state, in any organisation (addresses are unique store-wide). */
  getByEmail(email: string): Promise<UserDto | null>;

  // --- lists
  listLiveByIds(orgId: string, ids: string[]): Promise<UserDto[]>;
  /** Display names of live accounts among these ids (callers pass well-formed ids). */
  listNamesByIds(orgId: string, ids: string[]): Promise<MemberSummaryDto[]>;
  listAllLive(orgId: string): Promise<UserDto[]>;
  /** Name, role and sign-in state for graph-listed users (removed ones included). */
  listSummariesByIds(
    orgId: string,
    ids: string[],
  ): Promise<Array<Pick<UserDto, '_id' | 'hasLoggedIn' | 'fullName' | 'role'>>>;
  listDirectory(
    orgId: string,
    q: DirectoryQuery,
  ): Promise<{ rows: UserDto[]; total: number }>;
  /** Every live account of the org with its groups; service accounts only with `includeServiceAccounts`. */
  listWithGroups(
    orgId: string,
    opts?: { includeServiceAccounts?: boolean },
  ): Promise<UserWithGroupsDto[]>;
  /** One page of a group's members; ids of accounts in another organisation are not listed. */
  listMembersPage(
    orgId: string,
    ids: string[],
    q: {
      search?: string;
      skip: number;
      limit: number;
      /** Service accounts are left out of the page and of the count unless this is set. */
      includeServiceAccounts?: boolean;
    },
  ): Promise<{ rows: MemberSummaryDto[]; total: number }>;
  /** Accounts at these addresses in this organisation, removed ones included. */
  listByEmails(orgId: string, emails: string[]): Promise<UserDto[]>;
  listAdminIds(orgId: string): Promise<string[]>;
  listServiceAccounts(orgId: string): Promise<UserDto[]>;
  /** Of these ids, the ones that are service accounts of this organisation. */
  serviceAccountIdsAmong(orgId: string, ids: string[]): Promise<string[]>;
  getServiceAccount(orgId: string, id: string): Promise<UserDto | null>;
  countActiveAdmins(orgId: string, tx?: Tx): Promise<number>;

  // --- address checks
  existsLiveEmail(email: string): Promise<boolean>;
  /** Another live account in this organisation holds the address. */
  emailTakenByOther(
    orgId: string,
    email: string,
    exceptId: string,
  ): Promise<boolean>;
  /** Any account, anywhere, other than this one holds the address. */
  emailTakenElsewhere(email: string, exceptId: string): Promise<boolean>;
  /** A removed account of this organisation holds the address. */
  hasRemovedAccount(orgId: string, email: string): Promise<boolean>;

  // --- writes
  /** Throws `UniqueConstraintError{key}` on a duplicate address or slug. */
  create(orgId: string, fields: UserFields): Promise<UserDto>;
  createMany(orgId: string, rows: UserFields[]): Promise<UserDto[]>;
  /** Writes the given fields on the live account; null when there is none. */
  updateProfile(
    orgId: string,
    id: string,
    fields: UserFields,
    tx?: Tx,
  ): Promise<UserDto | null>;
  /** Marks the account removed (kept for restore); null when there is none. */
  markDeleted(
    orgId: string,
    id: string,
    extra?: { signedOut?: boolean; deletedBy?: string },
  ): Promise<UserDto | null>;
  /** Brings removed accounts at these addresses back; returns them. */
  restoreByEmails(orgId: string, emails: string[]): Promise<UserDto[]>;
  /** Makes these people admins (service accounts excluded). */
  promoteToAdmin(orgId: string, ids: string[]): Promise<void>;
  setRole(orgId: string, id: string, role: UserRole): Promise<void>;
  /** Serialises concurrent admin changes in this organisation (inside a transaction). */
  lockOrgAdminChanges(orgId: string, tx: Tx): Promise<void>;
  /** Hard delete, to undo a create that did not finish. */
  deleteById(id: string): Promise<void>;
  /** Hard delete of a removed account (sample accounts); false when it was brought back meanwhile. */
  deleteRemoved(orgId: string, id: string): Promise<boolean>;
  listRemovedIdsByEmail(orgId: string, email: string): Promise<string[]>;
  /** Switch sample accounts at this domain on or off, except the caller's own. */
  setDisabledByEmailDomain(
    orgId: string,
    domain: string,
    exceptId: string | undefined,
    disabled: boolean,
  ): Promise<number>;
  /** Moves the account to this address; null when it does not exist. */
  changeEmail(id: string, email: string): Promise<UserDto | null>;
  /** Rewrites an address without validation of ownership (token-verified UPN fix). */
  correctEmail(id: string, email: string): Promise<void>;

  // --- service accounts (same table, `kind: 'service'`)
  restoreServiceAccount(
    orgId: string,
    id: string,
    input: RestoreServiceAccountInput,
  ): Promise<UserDto | null>;
  /** Puts a half-restored account back; only when `restoreOpId` still marks this attempt. */
  abortServiceAccountRestore(id: string, restoreOpId: string): Promise<void>;
  /** Re-enables a restored account; null when something else moved it on. */
  finishServiceAccountRestore(
    id: string,
    restoreOpId: string,
  ): Promise<UserDto | null>;
  /** Enable a service account unless a restore is in flight. */
  enableServiceAccount(orgId: string, id: string): Promise<boolean>;
  /** False when no live service account matched (it was deleted since it was read): nothing was written. */
  updateServiceAccount(
    orgId: string,
    id: string,
    changes: Record<string, unknown>,
  ): Promise<boolean>;
}
