/**
 * Verifier mappings for documents, projects, notifications, the outbox, the mail log, connector settings, OAuth apps and tokens,
 * delegations and Slack links. Written from the Mongoose schemas and the tables; nothing here imports a repository.
 */
import type { CollectionMapping, FieldMap, IgnoredCollection } from './types';

const ORG: FieldMap = { col: 'org_id', path: 'orgId', type: 'oid' };
const CREATED: FieldMap[] = [
  { col: 'created_at', path: 'createdAt', type: 'date' },
  { col: 'updated_at', path: 'updatedAt', type: 'date' },
];
const SCOPE_LISTS = { apps: [], kb: [], recordGroups: [], records: [] };

const documents: CollectionMapping = {
  collection: 'documents',
  nullOk: ['initiatorUserId'],
  repository: 'document',
  table: 'content.documents',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'document_name', path: 'documentName', type: 'text' },
    {
      col: 'alternate_document_name',
      path: 'alternateDocumentName',
      type: 'text',
    },
    { col: 'document_path', path: 'documentPath', type: 'text' },
    {
      col: 'is_versioned_file',
      path: 'isVersionedFile',
      type: 'bool',
      readAbsentAs: false,
    },
    {
      col: 'mutation_count',
      path: 'mutationCount',
      type: 'int',
      readAbsentAs: 1,
    },
    { col: 'permissions', path: 'permissions', type: 'text' },
    { col: 'initiator_user_id', path: 'initiatorUserId', type: 'oid' },
    { col: 'size_in_bytes', path: 'sizeInBytes', type: 'bigint' },
    { col: 'mime_type', path: 'mimeType', type: 'text' },
    { col: 'extension', path: 'extension', type: 'text' },
    {
      col: 'version_history',
      path: 'versionHistory',
      type: 'json',
      readAbsentAs: [],
    },
    {
      col: 'custom_metadata',
      path: 'customMetadata',
      type: 'json',
      readAbsentAs: [],
    },
    {
      col: 'current_version',
      path: 'currentVersion',
      type: 'int',
      readAbsentAs: 0,
    },
    { col: 'tags', path: 'tags', type: 'text_array', readAbsentAs: [] },
    { col: 'deleted_by_user_id', path: 'deletedByUserId', type: 'text' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 's3', path: 's3', type: 'json' },
    { col: 'azure_blob', path: 'azureBlob', type: 'json' },
    { col: 'local', path: 'local', type: 'json' },
    { col: 'storage_vendor', path: 'storageVendor', type: 'text' },
    { col: 'idempotency_key', path: 'idempotencyKey', type: 'text' },
    {
      col: 'idempotency_fingerprint',
      path: 'idempotencyFingerprint',
      type: 'text',
    },
    { col: 'upload_lease_token', path: 'uploadLeaseToken', type: 'text' },
    {
      col: 'upload_lease_expires_at',
      path: 'uploadLeaseExpiresAt',
      type: 'epoch_ms',
    },
    {
      col: 'awaiting_direct_upload',
      path: 'awaitingDirectUpload',
      type: 'bool',
    },
    { col: 'artifact_set', path: 'artifactSet', type: 'json' },
    {
      col: 'retired_at',
      path: 'retiredAt',
      type: 'bigint',
      presence: 'null',
      readAbsentAs: null,
    },
    { col: 'write_seq', path: 'writeSeq', type: 'bigint', absentWhen: 0 },
    { col: 'writers', path: 'writers', type: 'json', absentWhen: [] },
    { col: 'created_at', path: 'createdAt', type: 'epoch_ms' },
    { col: 'updated_at', path: 'updatedAt', type: 'epoch_ms' },
  ],
  // connector_id repeats the customMetadata entry; initiator_unset records that the field was never stored
  derived: ['connector_id', 'initiator_unset'],
  transforms: [
    {
      // PostgreSQL keeps "created without an initiator" and "initiator null" apart in initiator_unset, which the verifier's
      // column-to-path mapping cannot read; a stored null is compared as absent (the column is NULL in both cases)
      name: 'initiatorUserId null compared as absent (initiator_unset keeps the difference)',
      apply: (doc) => {
        if (doc.initiatorUserId !== null) return false;
        delete doc.initiatorUserId;
        return true;
      },
    },
  ],
};

const projects: CollectionMapping = {
  collection: 'projects',
  repository: 'project',
  table: 'content.projects',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'name', path: 'name', type: 'text' },
    { col: 'description', path: 'description', type: 'text' },
    { col: 'icon', path: 'icon', type: 'text' },
    { col: 'color', path: 'color', type: 'text' },
    { col: 'instructions', path: 'instructions', type: 'text' },
    {
      col: 'knowledge_scope',
      path: 'knowledgeScope',
      type: 'json',
      readAbsentAs: SCOPE_LISTS,
    },
    {
      col: 'applied_filters',
      path: 'appliedFilters',
      type: 'json',
      readAbsentAs: SCOPE_LISTS,
    },
    { col: 'tools', path: 'tools', type: 'text_array', readAbsentAs: [] },
    {
      col: 'linked_knowledge_base_id',
      path: 'linkedKnowledgeBaseId',
      type: 'text',
      presence: 'null',
      readAbsentAs: null,
    },
    {
      col: 'visibility',
      path: 'visibility',
      type: 'text',
      readAbsentAs: 'private',
    },
    {
      col: 'chat_sharing',
      path: 'chatSharing',
      type: 'text',
      readAbsentAs: 'private',
    },
    { col: 'is_pinned', path: 'isPinned', type: 'bool', readAbsentAs: false },
    { col: 'pinned_by', path: 'pinnedBy', type: 'oid_array', readAbsentAs: [] },
    {
      col: 'is_archived',
      path: 'isArchived',
      type: 'bool',
      readAbsentAs: false,
    },
    { col: 'archived_by', path: 'archivedBy', type: 'oid' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'oid' },
    { col: 'last_activity_at', path: 'lastActivityAt', type: 'epoch_ms' },
    { col: 'metadata', path: 'metadata', type: 'json' },
    ...CREATED,
  ],
  children: [
    {
      table: 'content.project_members',
      parentCol: 'project_id',
      path: 'members',
      orderCol: 'pos',
      fields: [
        { col: 'principal_type', path: 'principalType', type: 'text' },
        { col: 'principal_id', path: 'principalId', type: 'text' },
        { col: 'role', path: 'role', type: 'text' },
        { col: 'added_by', path: 'addedBy', type: 'oid' },
        { col: 'added_at', path: 'addedAt', type: 'date' },
      ],
      derived: ['org_id'],
    },
  ],
  derived: [],
};

const notifications: CollectionMapping = {
  collection: 'notifications',
  repository: 'notification',
  table: 'platform.notifications',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'type', path: 'type', type: 'text' },
    { col: 'title', path: 'title', type: 'text' },
    { col: 'message', path: 'message', type: 'text' },
    { col: 'redirect_link', path: 'redirectLink', type: 'text' },
    { col: 'severity', path: 'severity', type: 'text' },
    { col: 'status', path: 'status', type: 'text', readAbsentAs: 'unread' },
    { col: 'origin_service', path: 'originService', type: 'text' },
    { col: 'assigned_to', path: 'assignedTo', type: 'oid' },
    { col: 'payload', path: 'payload', type: 'json' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'oid' },
    ...CREATED,
  ],
  derived: [],
};

/** The organisation of an event: `payload.orgId` of the event in `value`, else the one in the ordering key. */
function outboxOrg(doc: Record<string, unknown>): string | null {
  const hex = /^[0-9a-fA-F]{24}$/;
  try {
    const org = (
      (JSON.parse(String(doc.value)) as { payload?: { orgId?: unknown } })
        .payload ?? {}
    ).orgId;
    if (typeof org === 'string' && hex.test(org)) return org.toLowerCase();
  } catch {
    // value is not JSON
  }
  const m = /^(?:user|org):([0-9a-fA-F]{24})/.exec(
    typeof doc.orderingKey === 'string' ? doc.orderingKey : '',
  );
  return m ? (m[1] as string).toLowerCase() : null;
}

/** The table id an outbox event gets: seconds since 2020 x 2^21 plus the low 21 bits of the ObjectId counter. */
export function outboxTableId(hex: string): string {
  const secs = Math.max(0, parseInt(hex.slice(0, 8), 16) - 1577836800);
  const counter = parseInt(hex.slice(18, 24), 16) % 2097152;
  return String(secs * 2097152 + counter);
}

const outbox: CollectionMapping = {
  collection: 'outbox_events',
  repository: 'outbox',
  table: 'platform.outbox',
  idCol: 'id',
  idType: 'bigint',
  tableId: outboxTableId,
  sourceKey: (doc) => String(doc._id),
  orgPath: '__org',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    { col: 'org_id', path: '__org', type: 'oid' },
    { col: 'topic', path: 'topic', type: 'text' },
    { col: 'msg_key', path: 'key', type: 'text' },
    {
      col: 'ordering_key',
      path: 'orderingKey',
      type: 'text',
      readAbsentAs: 'global',
    },
    { col: 'value', path: 'value', type: 'text' },
    { col: 'headers', path: 'headers', type: 'json' },
    { col: 'status', path: 'status', type: 'text', readAbsentAs: 'pending' },
    { col: 'attempts', path: 'attempts', type: 'int', readAbsentAs: 0 },
    { col: 'next_attempt_at', path: 'nextAttemptAt', type: 'date' },
    { col: 'last_error', path: 'lastError', type: 'text' },
    ...CREATED,
  ],
  // the claim time and the delivery time are not copied: what is copied has not been delivered
  derived: ['claimed_at', 'published_at'],
  dropped: [
    { path: 'claimedAt', reason: 'a claim dies with the stopped dispatcher' },
    {
      path: 'publishedAt',
      reason: 'delivery time of rows that are not copied',
    },
  ],
  skip: (doc) => {
    if (doc.status === 'published')
      return 'published events are history and are not copied';
    return outboxOrg(doc)
      ? null
      : 'event names no organisation (not storable per organisation)';
  },
  transforms: [
    {
      name: 'event id folded into the table id (seconds since 2020 x 2^21 + counter & 0x1fffff)',
      apply: (doc) => {
        doc._id = outboxTableId(String(doc._id));
        return true;
      },
    },
    {
      name: 'organisation read from the event',
      apply: (doc) => {
        doc.__org = outboxOrg(doc);
        return true;
      },
    },
    {
      name: 'publishing becomes pending',
      apply: (doc) => {
        if (doc.status !== 'publishing') return false;
        doc.status = 'pending';
        return true;
      },
    },
    {
      name: 'claim time not copied',
      apply: (doc) => {
        if (doc.claimedAt === undefined) return false;
        delete doc.claimedAt;
        return true;
      },
    },
  ],
};

const mailInfo: CollectionMapping = {
  collection: 'mailInfo',
  repository: 'mail-log',
  table: 'platform.mail_log',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'subject', path: 'subject', type: 'text' },
    { col: 'from_address', path: 'from', type: 'text' },
    { col: 'to_addresses', path: 'to', type: 'text_array', readAbsentAs: [] },
    { col: 'cc_addresses', path: 'cc', type: 'text_array', readAbsentAs: [] },
    { col: 'email_template_type', path: 'emailTemplateType', type: 'text' },
    ...CREATED,
  ],
  derived: [],
  skip: (doc) =>
    doc.orgId === undefined || doc.orgId === null
      ? 'mail log entry without an organisation (the table is per organisation)'
      : null,
};

const connectorsConfig: CollectionMapping = {
  collection: 'connectorsConfig',
  repository: 'connectors-config',
  table: 'platform.connectors_config',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'name', path: 'name', type: 'text' },
    { col: 'is_enabled', path: 'isEnabled', type: 'bool', readAbsentAs: true },
    { col: 'last_updated_by', path: 'lastUpdatedBy', type: 'oid' },
    ...CREATED,
  ],
  derived: [],
};

const oauthApps: CollectionMapping = {
  collection: 'oauthApps',
  repository: 'oauth-app',
  table: 'oauth.apps',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'slug', path: 'slug', type: 'text' },
    { col: 'client_id', path: 'clientId', type: 'text' },
    {
      col: 'client_secret_encrypted',
      path: 'clientSecretEncrypted',
      type: 'text',
    },
    { col: 'name', path: 'name', type: 'text' },
    { col: 'description', path: 'description', type: 'text' },
    { col: 'created_by', path: 'createdBy', type: 'oid' },
    { col: 'token_identity_user_id', path: 'tokenIdentityUserId', type: 'oid' },
    {
      col: 'redirect_uris',
      path: 'redirectUris',
      type: 'text_array',
      readAbsentAs: [],
    },
    {
      col: 'allowed_grant_types',
      path: 'allowedGrantTypes',
      type: 'text_array',
      readAbsentAs: ['authorization_code', 'refresh_token'],
    },
    { col: 'allowed_scopes', path: 'allowedScopes', type: 'text_array' },
    { col: 'status', path: 'status', type: 'text', readAbsentAs: 'active' },
    { col: 'logo_url', path: 'logoUrl', type: 'text' },
    { col: 'homepage_url', path: 'homepageUrl', type: 'text' },
    { col: 'privacy_policy_url', path: 'privacyPolicyUrl', type: 'text' },
    { col: 'terms_of_service_url', path: 'termsOfServiceUrl', type: 'text' },
    {
      col: 'is_confidential',
      path: 'isConfidential',
      type: 'bool',
      readAbsentAs: true,
    },
    { col: 'is_dynamic', path: 'isDynamic', type: 'bool', readAbsentAs: false },
    {
      col: 'access_token_lifetime',
      path: 'accessTokenLifetime',
      type: 'int',
      readAbsentAs: 3600,
    },
    {
      col: 'refresh_token_lifetime',
      path: 'refreshTokenLifetime',
      type: 'int',
      readAbsentAs: 2592000,
    },
    { col: 'knowledge_limit', path: 'knowledgeLimit', type: 'json' },
    { col: 'is_deleted', path: 'isDeleted', type: 'bool', readAbsentAs: false },
    { col: 'deleted_by', path: 'deletedBy', type: 'oid' },
    ...CREATED,
  ],
  derived: [],
};

const REVOCATION: FieldMap[] = [
  { col: 'is_revoked', path: 'isRevoked', type: 'bool', readAbsentAs: false },
  { col: 'revoked_at', path: 'revokedAt', type: 'date' },
  { col: 'revoked_by', path: 'revokedBy', type: 'oid' },
  { col: 'revoked_reason', path: 'revokedReason', type: 'text' },
];

const oauthAccessTokens: CollectionMapping = {
  collection: 'oauthAccessTokens',
  repository: 'oauth-token',
  table: 'oauth.access_tokens',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'token_hash', path: 'tokenHash', type: 'text' },
    { col: 'client_id', path: 'clientId', type: 'text' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'scopes', path: 'scopes', type: 'text_array' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    ...REVOCATION,
    {
      col: 'parent_refresh_token_id',
      path: 'parentRefreshTokenId',
      type: 'oid',
    },
    { col: 'name', path: 'name', type: 'text' },
    { col: 'last_used_at', path: 'lastUsedAt', type: 'date' },
    { col: 'actor_user_id', path: 'actorUserId', type: 'oid' },
    { col: 'knowledge_limit', path: 'knowledgeLimit', type: 'json' },
    ...CREATED,
  ],
  derived: [],
};

const oauthRefreshTokens: CollectionMapping = {
  collection: 'oauthRefreshTokens',
  repository: 'oauth-token',
  table: 'oauth.refresh_tokens',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    { col: 'token_hash', path: 'tokenHash', type: 'text' },
    { col: 'client_id', path: 'clientId', type: 'text' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'scopes', path: 'scopes', type: 'text_array' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    ...REVOCATION,
    {
      col: 'rotation_count',
      path: 'rotationCount',
      type: 'int',
      readAbsentAs: 0,
    },
    { col: 'previous_token_hash', path: 'previousTokenHash', type: 'text' },
    { col: 'knowledge_limit', path: 'knowledgeLimit', type: 'json' },
    ...CREATED,
  ],
  derived: [],
};

const authorizationCodes: CollectionMapping = {
  collection: 'authorizationCodes',
  repository: 'oauth-token',
  table: 'oauth.authorization_codes',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ORG,
    // the column holds the SHA-256 of the code; the verifier hashes the source value before comparing
    { col: 'code_hash', path: 'code', type: 'sha256' },
    { col: 'code_challenge', path: 'codeChallenge', type: 'text' },
    { col: 'code_challenge_method', path: 'codeChallengeMethod', type: 'text' },
    { col: 'client_id', path: 'clientId', type: 'text' },
    { col: 'user_id', path: 'userId', type: 'oid' },
    { col: 'redirect_uri', path: 'redirectUri', type: 'text' },
    { col: 'scopes', path: 'scopes', type: 'text_array' },
    { col: 'knowledge_limit', path: 'knowledgeLimit', type: 'json' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    { col: 'is_used', path: 'isUsed', type: 'bool', readAbsentAs: false },
    { col: 'used_at', path: 'usedAt', type: 'date' },
    { col: 'replayed_at', path: 'replayedAt', type: 'date' },
    ...CREATED,
  ],
  derived: [],
};

/**
 * Pending device sign-ins live minutes, and the application keeps them all in the install's organisation. They are copied so a
 * person who is in the middle of one when the store moves does not have to start again; the generic comparison does not apply
 * (`custom`: `verify.ts` checks that every unexpired one is there).
 */
const oauthDeviceCodes: CollectionMapping = {
  collection: 'oauthDeviceCodes',
  repository: 'oauth-token',
  table: 'oauth.device_authorizations',
  idCol: 'id',
  orgPath: null,
  orgCol: null,
  custom: true,
  versionCol: 'version',
  fields: [
    { col: 'device_code_hash', path: 'deviceCodeHash', type: 'text' },
    { col: 'user_code', path: 'userCode', type: 'text' },
    { col: 'client_id', path: 'clientId', type: 'text' },
    { col: 'scopes', path: 'scopes', type: 'text_array' },
    { col: 'status', path: 'status', type: 'text', readAbsentAs: 'pending' },
    { col: 'approved_user_id', path: 'userId', type: 'oid' },
    { col: 'poll_interval', path: 'interval', type: 'int', readAbsentAs: 5 },
    { col: 'last_polled_at', path: 'lastPolledAt', type: 'date' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    { col: 'limit_apps', path: 'knowledgeLimit.apps', type: 'text_array' },
    {
      col: 'limit_groups',
      path: 'knowledgeLimit.recordGroups',
      type: 'text_array',
    },
    {
      col: 'limit_records',
      path: 'knowledgeLimit.records',
      type: 'text_array',
    },
    ...CREATED,
  ],
  censusKnown: [
    'deviceCodeHash',
    'userCode',
    'clientId',
    'scopes',
    'scopes[]',
    'status',
    'userId',
    'orgId',
    'interval',
    'lastPolledAt',
    'expiresAt',
    'knowledgeLimit',
    'knowledgeLimit.apps',
    'knowledgeLimit.apps[]',
    'knowledgeLimit.recordGroups',
    'knowledgeLimit.recordGroups[]',
    'knowledgeLimit.records',
    'knowledgeLimit.records[]',
    'knowledgeLimit[]',
    'knowledgeLimit[].apps',
    'knowledgeLimit[].recordGroups',
    'knowledgeLimit[].records',
    'createdAt',
    'updatedAt',
    '__v',
  ],
  // the install's organisation holds every pending device authorization (see the importer)
  derived: ['org_id'],
};

const slackConversations: CollectionMapping = {
  collection: 'slack_conversations',
  repository: 'slack-thread',
  table: 'integrations.slack_threads',
  // The table's key is (org, thread, bot, email) and the Mongo `_id` is not stored: documents are matched on the thread id.
  idCol: 'thread_id',
  sourceKey: (doc) => String(doc._id),
  orgPath: null,
  orgCol: null,
  versionCol: 'version',
  fields: [
    { col: 'bot_id', path: 'botId', type: 'text' },
    { col: 'email', path: 'email', type: 'text' },
    { col: 'conversation_id', path: 'conversationId', type: 'text' },
    { col: 'thread_id', path: 'threadId', type: 'text' },
    ...CREATED,
  ],
  // the organisation is that of the conversation the link continues
  derived: ['org_id'],
  transforms: [
    {
      name: '_id read as the thread id (the table does not keep the document id)',
      apply: (doc) => {
        doc._id = doc.threadId;
        return true;
      },
    },
  ],
};

const DELEGATION_COMMON: FieldMap[] = [
  ORG,
  { col: 'service_account_id', path: 'serviceAccountId', type: 'oid' },
];

const serviceAccountDelegations: CollectionMapping = {
  collection: 'serviceAccountDelegations',
  repository: 'delegation',
  table: 'oauth.service_account_delegations',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ...DELEGATION_COMMON,
    { col: 'subject_user_id', path: 'subjectUserId', type: 'oid' },
    { col: 'granted_by', path: 'grantedBy', type: 'oid' },
    { col: 'reason', path: 'reason', type: 'text' },
    ...CREATED,
  ],
  derived: [],
};

const delegationEvents: CollectionMapping = {
  collection: 'delegationEvents',
  repository: 'delegation',
  table: 'oauth.delegation_events',
  idCol: 'id',
  orgPath: 'orgId',
  orgCol: 'org_id',
  versionCol: 'version',
  fields: [
    ...DELEGATION_COMMON,
    { col: 'subject_user_id', path: 'subjectUserId', type: 'oid' },
    {
      col: 'requested_subject',
      path: 'requestedSubject',
      type: 'text',
      readAbsentAs: '',
    },
    { col: 'outcome', path: 'outcome', type: 'text' },
    { col: 'refused_reason', path: 'refusedReason', type: 'text' },
    { col: 'acted_by', path: 'actedBy', type: 'oid' },
    { col: 'note', path: 'note', type: 'text' },
    { col: 'scopes', path: 'scopes', type: 'text_array', readAbsentAs: [] },
    { col: 'access_token_id', path: 'accessTokenId', type: 'oid' },
    { col: 'jti', path: 'jti', type: 'text' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    ...CREATED,
  ],
  derived: [],
};

/**
 * Agent registrations are kept in the install's organisation, like device authorizations. Unlike those they back an identity
 * assertion for weeks, so a copy keeps every row the sweeper has not yet reached (`custom`: `verify.ts` checks that each is there).
 */
const oauthAgentRegistrations: CollectionMapping = {
  collection: 'oauthAgentRegistrations',
  repository: 'oauth-token',
  table: 'oauth.agent_registrations',
  idCol: 'id',
  orgPath: null,
  orgCol: null,
  custom: true,
  versionCol: 'version',
  fields: [
    { col: 'claim_email', path: 'claimEmail', type: 'text' },
    { col: 'claim_token_hash', path: 'claimTokenHash', type: 'text' },
    { col: 'attempt_token_hash', path: 'attemptTokenHash', type: 'text' },
    { col: 'user_code_hash', path: 'userCodeHash', type: 'text' },
    { col: 'attempt_expires_at', path: 'attemptExpiresAt', type: 'date' },
    { col: 'expires_at', path: 'expiresAt', type: 'date' },
    { col: 'purge_at', path: 'purgeAt', type: 'date' },
    { col: 'status', path: 'status', type: 'text', readAbsentAs: 'pending' },
    { col: 'failed_attempts', path: 'failedAttempts', type: 'int', readAbsentAs: 0 },
    { col: 'last_polled_at', path: 'lastPolledAt', type: 'date' },
    { col: 'claimed_user_id', path: 'claimedUserId', type: 'oid' },
    { col: 'claimed_org_id', path: 'claimedOrgId', type: 'oid' },
    { col: 'approved_scopes', path: 'approvedScopes', type: 'text_array' },
    ...CREATED,
  ],
  censusKnown: [
    'claimEmail',
    'claimTokenHash',
    'attemptTokenHash',
    'userCodeHash',
    'attemptExpiresAt',
    'expiresAt',
    'purgeAt',
    'status',
    'failedAttempts',
    'lastPolledAt',
    'claimedUserId',
    'claimedOrgId',
    'approvedScopes',
    'approvedScopes[]',
    'createdAt',
    'updatedAt',
    '__v',
  ],
  derived: ['org_id'],
};

/** Parents before children: apps before their tokens and codes; Slack links after the chat sessions they continue (copied earlier). */
const LISTED: CollectionMapping[] = [
  documents,
  projects,
  notifications,
  outbox,
  mailInfo,
  connectorsConfig,
  oauthApps,
  oauthAccessTokens,
  oauthRefreshTokens,
  authorizationCodes,
  oauthDeviceCodes,
  oauthAgentRegistrations,
  slackConversations,
  serviceAccountDelegations,
  delegationEvents,
];

/** A document written by a raw driver call has no `__v`; the column is NOT NULL, so such a document is read as holding 0 (counted). */
const ABSENT_VERSION = {
  name: 'absent __v read as 0 (the column is NOT NULL)',
  apply: (doc: Record<string, unknown>): boolean => {
    if (doc.__v !== undefined) return false;
    doc.__v = 0;
    return true;
  },
};

export const CONTENT_MAPPINGS: CollectionMapping[] = LISTED.map((m) =>
  m.versionCol
    ? { ...m, transforms: [...(m.transforms ?? []), ABSENT_VERSION] }
    : m,
);

export const CONTENT_IGNORED: IgnoredCollection[] = [
  {
    collection: 'crawlingManagerConfigs',
    reason:
      'dead model: no code reads or writes it (the crawl schedule lives in the job queue); preflight requires it to be empty or reports the documents',
  },
];
