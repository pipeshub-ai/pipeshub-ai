import type { CollectionMapping } from './types';

export const org: CollectionMapping = {
  collection: 'org',
  repository: 'org',
  table: 'identity.orgs',
  idCol: 'org_id',
  orgPath: '_id',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'slug', path: 'slug', type: 'text' },
    { col: 'registered_name', path: 'registeredName', type: 'text' },
    { col: 'short_name', path: 'shortName', type: 'text' },
    { col: 'domain', path: 'domain', type: 'text' },
    { col: 'contact_email', path: 'contactEmail', type: 'text' },
    { col: 'account_type', path: 'accountType', type: 'text' },
    { col: 'phone_number', path: 'phoneNumber', type: 'text' },
    { col: 'permanent_address', path: 'permanentAddress', type: 'json' },
    { col: 'on_boarding_status', path: 'onBoardingStatus', type: 'text' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by_user', path: 'deletedByUser', type: 'text' },
    { col: 'admin_role_guard_at', path: 'adminRoleGuardAt', type: 'date' },
    { col: 'signup_channel', path: 'signupChannel', type: 'text' },
    {
      col: 'signup_referrer_domain',
      path: 'signupReferrerDomain',
      type: 'text',
    },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
};

export const orgLogos: CollectionMapping = {
  collection: 'org-logos',
  repository: 'org',
  table: 'identity.org_logos',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    {
      col: 'logo',
      path: 'logo',
      type: 'text',
      presence: 'null',
      readAbsentAs: null,
    },
    { col: 'logo_url', path: 'logoUrl', type: 'text' },
    {
      col: 'mime_type',
      path: 'mimeType',
      type: 'text',
      presence: 'null',
      readAbsentAs: null,
    },
  ],
  derived: [],
};

const noVersion = [
  { path: '__v', reason: 'the table keeps no version for this collection' },
];

export const users: CollectionMapping = {
  collection: 'users',
  repository: 'user',
  table: 'identity.users',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'slug', path: 'slug', type: 'text' },
    { col: 'full_name', path: 'fullName', type: 'text' },
    { col: 'first_name', path: 'firstName', type: 'text' },
    { col: 'last_name', path: 'lastName', type: 'text' },
    { col: 'middle_name', path: 'middleName', type: 'text' },
    { col: 'email', path: 'email', type: 'text' },
    { col: 'mobile', path: 'mobile', type: 'text' },
    {
      col: 'has_logged_in',
      path: 'hasLoggedIn',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'designation', path: 'designation', type: 'text' },
    { col: 'kind', path: 'kind', type: 'text', readAbsentAs: 'human' },
    { col: 'description', path: 'description', type: 'text' },
    {
      col: 'is_disabled',
      path: 'isDisabled',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'restore_op_id', path: 'restoreOpId', type: 'text' },
    { col: 'role', path: 'role', type: 'text', readAbsentAs: 'member' },
    { col: 'address', path: 'address', type: 'json' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'text' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
  transforms: [
    {
      name: 'email lower-cased',
      apply: (d) => {
        if (typeof d.email !== 'string' || d.email === d.email.toLowerCase())
          return false;
        d.email = d.email.toLowerCase();
        return true;
      },
    },
  ],
};

export const userGroups: CollectionMapping = {
  collection: 'userGroups',
  repository: 'group',
  table: 'identity.user_groups',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'slug', path: 'slug', type: 'text' },
    { col: 'name', path: 'name', type: 'text' },
    { col: 'type', path: 'type', type: 'text' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'oid' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  children: [
    {
      table: 'identity.user_group_members',
      parentCol: 'group_id',
      path: 'users',
      orderCol: 'pos',
      fields: [{ col: 'user_id', path: '', type: 'oid' }],
      derived: ['org_id'],
    },
  ],
  derived: [],
  transforms: [
    {
      name: 'member listed twice kept once',
      apply: (d) => {
        if (!Array.isArray(d.users)) return false;
        const seen = new Set<string>();
        const kept = (d.users as unknown[]).filter((u) => {
          const k = String(u).toLowerCase();
          if (seen.has(k)) return false;
          seen.add(k);
          return true;
        });
        if (kept.length === d.users.length) return false;
        d.users = kept;
        return true;
      },
    },
  ],
};

/**
 * A credential is keyed by its account (organisation, user) in the target, not by its own `_id`, and several credentials of one account
 * become one row (the newest wins), so the generic document comparison skips them: `credentials.ts` compares them per account.
 */
export const userCredentials: CollectionMapping = {
  collection: 'userCredentials',
  // the copy offers only the newest credential of each account (credentials.ts), so it can read them in batches like any collection
  repository: 'credential',
  table: 'identity.user_credentials',
  idCol: 'user_id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'password_hash', path: 'hashedPassword', type: 'text' },
    {
      col: 'wrong_count',
      path: 'wrongCredentialCount',
      type: 'int',
      readAbsentAs: 0,
    },
    { col: 'is_blocked', path: 'isBlocked', type: 'bool', readAbsentAs: false },
    {
      col: 'block_expires_at',
      path: 'blockExpiresAt',
      type: 'date',
      presence: 'null',
      readAbsentAs: null,
    },
    { col: 'otp_hash', path: 'hashedOTP', type: 'text' },
    { col: 'otp_valid_until', path: 'otpValidity', type: 'bigint' },
    {
      col: 'force_new_password',
      path: 'forceNewPasswordGeneration',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'ip_address', path: 'ipAddress', type: 'text' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
  dropped: [
    ...noVersion,
    {
      path: 'userId',
      reason: 'the row is keyed by the account; the account id is its key',
    },
    { path: 'isDeleted', reason: 'credentials marked deleted are not copied' },
  ],
  skip: () => 'compared per account by credentials.ts',
  custom: true,
};

export const orgAuthConfig: CollectionMapping = {
  collection: 'orgAuthConfig',
  repository: 'org-auth-config',
  table: 'identity.org_auth_config',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'domain', path: 'domain', type: 'text' },
    { col: 'auth_steps', path: 'authSteps', type: 'json' },
    {
      col: 'removed_sign_in_methods',
      path: 'removedSignInMethods',
      type: 'text_array',
      readAbsentAs: [],
    },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
};

export const userActivities: CollectionMapping = {
  collection: 'userActivities',
  repository: 'activity',
  table: 'identity.user_activities',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'email', path: 'email', type: 'text' },
    { col: 'activity_type', path: 'activityType', type: 'text' },
    { col: 'login_mode', path: 'loginMode', type: 'text' },
    { col: 'ip_address', path: 'ipAddress', type: 'text' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
  dropped: noVersion,
  // an attempt from before any organisation was known has no row here (the activity importer reports it as skipped)
  skip: (d) =>
    d.orgId === undefined || d.orgId === null
      ? 'activity without an organisation'
      : null,
  keepOnReverse: { orgId: null },
};

export const usedPasswordResetLinks: CollectionMapping = {
  collection: 'usedPasswordResetLinks',
  repository: 'reset-link',
  table: 'identity.used_reset_links',
  // the link hash is unique per organisation; the document id is not stored
  idCol: 'link_hash',
  orgPath: 'orgId',
  orgCol: 'org_id',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'user_id', path: 'userId', type: 'text' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    { col: 'created_at', path: 'createdAt', type: 'date' },
    { col: 'updated_at', path: 'updatedAt', type: 'date' },
  ],
  derived: [],
  dropped: noVersion,
  censusKnown: ['linkHash'],
  sourceKey: (doc) => String(doc._id),
  transforms: [
    {
      name: 'claim identified by its link hash (document id not stored)',
      apply: (d) => {
        d._id = d.linkHash;
        delete d.linkHash;
        return true;
      },
    },
  ],
};

export const userDps: CollectionMapping = {
  collection: 'user-dps',
  repository: 'picture',
  table: 'identity.user_pictures',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: 'orgId', type: 'oid' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    {
      col: 'pic',
      path: 'pic',
      type: 'text',
      presence: 'null',
      readAbsentAs: null,
    },
    { col: 'pic_url', path: 'picUrl', type: 'text' },
    {
      col: 'mime_type',
      path: 'mimeType',
      type: 'text',
      presence: 'null',
      readAbsentAs: null,
    },
  ],
  derived: [],
};

/**
 * Counters are per name in the source and per organisation and kind in the target, and the organisation counter becomes a sequence, so
 * the generic comparison skips them; `counterViolations` in `verify-identity.ts` checks the values instead.
 */
export const counters: CollectionMapping = {
  collection: 'counters',
  repository: 'org',
  table: 'identity.slug_counters',
  idCol: 'kind',
  orgPath: null,
  orgCol: 'org_id',
  fields: [{ col: 'next_value', path: 'seq', type: 'int' }],
  derived: ['org_id'],
  dropped: [
    { path: 'name', reason: 'the counter name is the kind' },
    { path: '__v', reason: 'no version' },
  ],
  skip: () => 'checked by counterViolations (verify-identity.ts)',
  custom: true,
};

export const IDENTITY_MAPPINGS: CollectionMapping[] = [
  org,
  orgLogos,
  users,
  userGroups,
  userCredentials,
  orgAuthConfig,
  userActivities,
  usedPasswordResetLinks,
  userDps,
  counters,
];
