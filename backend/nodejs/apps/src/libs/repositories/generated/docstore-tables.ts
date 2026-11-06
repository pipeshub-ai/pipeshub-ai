// Generated from the migrated database (src/scripts/gen-db-types.ts). Do not edit by hand.
import type { Generated, GeneratedAlways } from 'kysely';

export interface ContentDocuments {
  org_id: string;
  id: string;
  document_name: string;
  alternate_document_name: string | null;
  document_path: string | null;
  is_versioned_file: Generated<boolean>;
  mutation_count: Generated<number>;
  permissions: string | null;
  initiator_user_id: string | null;
  initiator_unset: Generated<boolean>;
  size_in_bytes: string | null;
  mime_type: string | null;
  extension: string | null;
  version_history: Generated<unknown>;
  custom_metadata: Generated<unknown>;
  connector_id: string | null;
  current_version: Generated<number>;
  tags: Generated<string[]>;
  deleted_by_user_id: string | null;
  is_deleted: Generated<boolean>;
  s3: unknown;
  azure_blob: unknown;
  local: unknown;
  storage_vendor: string;
  idempotency_key: string | null;
  idempotency_fingerprint: string | null;
  upload_lease_token: string | null;
  upload_lease_expires_at: Date | null;
  awaiting_direct_upload: boolean | null;
  artifact_set: unknown;
  retired_at: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
  write_seq: Generated<string>;
  writers: Generated<unknown>;
}

export interface ContentProjectMembers {
  org_id: string;
  project_id: string;
  principal_type: string;
  principal_id: string;
  role: string;
  added_by: string;
  added_at: Date;
  pos: number;
}

export interface ContentProjects {
  org_id: string;
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  icon: string | null;
  color: string | null;
  instructions: string | null;
  knowledge_scope: Generated<unknown>;
  applied_filters: Generated<unknown>;
  tools: Generated<string[]>;
  linked_knowledge_base_id: string | null;
  visibility: Generated<string>;
  chat_sharing: Generated<string>;
  is_pinned: Generated<boolean>;
  pinned_by: Generated<string[]>;
  is_archived: Generated<boolean>;
  archived_by: string | null;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  last_activity_at: Date;
  metadata: unknown;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface ConversationChatMessages {
  org_id: string;
  session_id: string;
  seq: number;
  id: string;
  message_type: string;
  content: Generated<string>;
  content_format: string | null;
  status: string | null;
  confidence: string | null;
  citations: Generated<unknown>;
  follow_up_questions: Generated<unknown>;
  feedback: Generated<unknown>;
  metadata: unknown;
  model_info: unknown;
  applied_filters: unknown;
  attachments: Generated<unknown>;
  reference_data: Generated<unknown>;
  tools: Generated<unknown>;
  reasoning: Generated<unknown>;
  parts: Generated<unknown>;
  created_at: Date;
  updated_at: Date;
}

export interface ConversationChatSessionMembers {
  org_id: string;
  session_id: string;
  user_id: string;
  access_level: string;
  pos: number;
  entry_id: string;
}

export interface ConversationChatSessions {
  org_id: string;
  id: string;
  session_type: Generated<string>;
  user_id: string;
  initiator: string;
  title: string | null;
  is_shared: Generated<boolean>;
  share_link: string | null;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  is_archived: Generated<boolean>;
  archived_by: string | null;
  last_activity_at: Date | null;
  status: string | null;
  fail_reason: string | null;
  active_run_id: string | null;
  next_seq: Generated<number>;
  model_info: Generated<unknown>;
  conversation_errors: Generated<unknown>;
  metadata: unknown;
  agent_key: string | null;
  conversation_source: string | null;
  compacted_summary: string | null;
  compacted_at_turn_index: number | null;
  compacted_at_timestamp: string | null;
  project_id: string | null;
  project_visibility: string | null;
  is_pinned: Generated<boolean>;
  version: Generated<number>;
  nulls: Generated<string[]>;
  created_at: Date;
  updated_at: Date;
  is_temporary: Generated<boolean>;
  expires_at: Date | null;
}

export interface ConversationCitations {
  org_id: string;
  id: string;
  content: string;
  chunk_index: number;
  citation_type: string;
  record_id: string;
  record_name: string | null;
  metadata: unknown;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface ConversationSemanticSearchMembers {
  org_id: string;
  search_id: string;
  user_id: string;
  access_level: string;
  pos: number;
  entry_id: string;
}

export interface ConversationSemanticSearches {
  org_id: string;
  id: string;
  query: string;
  result_limit: number;
  user_id: string;
  citation_ids: Generated<string[]>;
  records: Generated<unknown>;
  is_shared: Generated<boolean>;
  share_link: string | null;
  is_archived: Generated<boolean>;
  archived_by: string | null;
  version: Generated<number>;
  nulls: Generated<string[]>;
  created_at: Date;
  updated_at: Date;
}

export interface IdentityOrgAuthConfig {
  org_id: string;
  id: string;
  domain: string | null;
  removed_sign_in_methods: Generated<string[]>;
  auth_steps: unknown;
  is_deleted: Generated<boolean>;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface IdentityOrgLogos {
  org_id: string;
  id: string;
  logo: string | null;
  logo_url: string | null;
  mime_type: string | null;
  version: Generated<number>;
}

export interface IdentityOrgs {
  org_id: string;
  slug: Generated<string>;
  registered_name: string | null;
  short_name: string | null;
  domain: string;
  contact_email: string;
  account_type: string;
  phone_number: string | null;
  permanent_address: unknown;
  on_boarding_status: string | null;
  is_deleted: Generated<boolean>;
  deleted_by_user: string | null;
  admin_role_guard_at: Date | null;
  signup_channel: string | null;
  signup_referrer_domain: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface IdentitySlugCounters {
  org_id: string;
  kind: string;
  next_value: number;
}

export interface IdentityUsedResetLinks {
  org_id: string;
  link_hash: string;
  user_id: string;
  expires_at: Date;
  created_at: Date;
  updated_at: Date;
}

export interface IdentityUserActivities {
  org_id: string;
  id: string;
  user_id: string | null;
  email: string | null;
  activity_type: string;
  login_mode: string | null;
  ip_address: string;
  is_deleted: Generated<boolean>;
  created_at: Date;
  updated_at: Date;
}

export interface IdentityUserCredentials {
  org_id: string;
  user_id: string;
  password_hash: string | null;
  wrong_count: Generated<number>;
  is_blocked: Generated<boolean>;
  block_expires_at: Date | null;
  otp_hash: string | null;
  otp_valid_until: string | null;
  force_new_password: Generated<boolean>;
  ip_address: string | null;
  created_at: Generated<Date>;
  updated_at: Generated<Date>;
}

export interface IdentityUserGroupMembers {
  org_id: string;
  group_id: string;
  user_id: string;
  pos: number;
}

export interface IdentityUserGroups {
  org_id: string;
  id: string;
  slug: string;
  name: string;
  type: string;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface IdentityUserPictures {
  org_id: string;
  user_id: string;
  id: string;
  pic: string | null;
  pic_url: string | null;
  mime_type: string | null;
  version: Generated<number>;
}

export interface IdentityUsers {
  org_id: string;
  id: string;
  slug: string;
  full_name: string | null;
  first_name: string | null;
  last_name: string | null;
  middle_name: string | null;
  email: string;
  mobile: string | null;
  has_logged_in: Generated<boolean>;
  designation: string | null;
  kind: Generated<string>;
  description: string | null;
  is_disabled: Generated<boolean>;
  restore_op_id: string | null;
  role: Generated<string>;
  address: unknown;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface IntegrationsSlackThreads {
  org_id: string;
  thread_id: string;
  bot_id: string;
  email: string;
  conversation_id: string;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthAccessTokens {
  org_id: string;
  id: string;
  token_hash: string;
  client_id: string;
  user_id: string | null;
  scopes: string[];
  expires_at: Date;
  is_revoked: Generated<boolean>;
  revoked_at: Date | null;
  revoked_by: string | null;
  revoked_reason: string | null;
  parent_refresh_token_id: string | null;
  name: string | null;
  last_used_at: Date | null;
  actor_user_id: string | null;
  knowledge_limit: unknown;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthAgentRegistrations {
  org_id: string;
  id: string;
  claim_email: string;
  claim_token_hash: string;
  attempt_token_hash: string;
  user_code_hash: string;
  attempt_expires_at: Date;
  expires_at: Date;
  purge_at: Date;
  status: Generated<string>;
  failed_attempts: Generated<number>;
  last_polled_at: Date | null;
  claimed_user_id: string | null;
  claimed_org_id: string | null;
  approved_scopes: string[] | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthApps {
  org_id: string;
  id: string;
  slug: string;
  client_id: string;
  client_secret_encrypted: string;
  name: string;
  description: string | null;
  created_by: string;
  token_identity_user_id: string | null;
  redirect_uris: Generated<string[]>;
  allowed_grant_types: Generated<string[]>;
  allowed_scopes: string[];
  status: Generated<string>;
  logo_url: string | null;
  homepage_url: string | null;
  privacy_policy_url: string | null;
  terms_of_service_url: string | null;
  is_confidential: Generated<boolean>;
  is_dynamic: Generated<boolean>;
  access_token_lifetime: Generated<number>;
  refresh_token_lifetime: Generated<number>;
  knowledge_limit: unknown;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthAuthorizationCodes {
  org_id: string;
  id: string;
  code_hash: string;
  code_challenge: string | null;
  code_challenge_method: string | null;
  client_id: string;
  user_id: string;
  redirect_uri: string;
  scopes: string[];
  expires_at: Date;
  is_used: Generated<boolean>;
  used_at: Date | null;
  replayed_at: Date | null;
  knowledge_limit: unknown;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthDelegationEvents {
  org_id: string;
  id: string;
  service_account_id: string;
  subject_user_id: string | null;
  requested_subject: string;
  outcome: string;
  refused_reason: string | null;
  acted_by: string | null;
  note: string | null;
  scopes: Generated<string[]>;
  access_token_id: string | null;
  jti: string | null;
  expires_at: Date | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthDeviceAuthorizations {
  org_id: string;
  id: string;
  device_code_hash: string;
  user_code: string;
  client_id: string;
  scopes: string[];
  status: Generated<string>;
  approved_user_id: string | null;
  poll_interval: Generated<number>;
  last_polled_at: Date | null;
  limit_apps: string[] | null;
  limit_groups: string[] | null;
  limit_records: string[] | null;
  expires_at: Date;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthRefreshTokens {
  org_id: string;
  id: string;
  token_hash: string;
  client_id: string;
  user_id: string;
  scopes: string[];
  expires_at: Date;
  is_revoked: Generated<boolean>;
  revoked_at: Date | null;
  revoked_by: string | null;
  revoked_reason: string | null;
  rotation_count: Generated<number>;
  previous_token_hash: string | null;
  knowledge_limit: unknown;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface OauthServiceAccountDelegations {
  org_id: string;
  id: string;
  service_account_id: string;
  subject_user_id: string;
  granted_by: string;
  reason: string | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface PlatformConnectorsConfig {
  org_id: string;
  name: string;
  id: string;
  is_enabled: Generated<boolean>;
  last_updated_by: string;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface PlatformDocstoreChanges {
  org_id: string;
  id: GeneratedAlways<string>;
  rel: string;
  pk: string;
  op: unknown;
  txid: string;
  at: Generated<Date>;
}

export interface PlatformMailLog {
  org_id: string;
  id: string;
  created_at: Date;
  subject: string | null;
  from_address: string;
  to_addresses: string[];
  cc_addresses: Generated<string[]>;
  email_template_type: string;
  version: Generated<number>;
  updated_at: Date;
}

export interface PlatformNotifications {
  org_id: string;
  id: string;
  created_at: Date;
  type: string;
  title: string | null;
  message: string | null;
  redirect_link: string | null;
  severity: string | null;
  status: Generated<string>;
  origin_service: string | null;
  assigned_to: string;
  payload: unknown;
  is_deleted: Generated<boolean>;
  deleted_by: string | null;
  version: Generated<number>;
  updated_at: Date;
}

export interface PlatformOutbox {
  org_id: string;
  id: Generated<string>;
  topic: string;
  msg_key: string;
  ordering_key: Generated<string>;
  value: string;
  headers: unknown;
  status: Generated<string>;
  attempts: Generated<number>;
  next_attempt_at: Date;
  claimed_at: Date | null;
  last_error: string | null;
  published_at: Date | null;
  version: Generated<number>;
  created_at: Date;
  updated_at: Date;
}

export interface PlatformStoreEpoch {
  one: Generated<boolean>;
  backend: string;
  epoch: number;
  readonly: Generated<boolean>;
  updated_at: Generated<Date>;
}

export interface DB {
  'content.documents': ContentDocuments;
  'content.project_members': ContentProjectMembers;
  'content.projects': ContentProjects;
  'conversation.chat_messages': ConversationChatMessages;
  'conversation.chat_session_members': ConversationChatSessionMembers;
  'conversation.chat_sessions': ConversationChatSessions;
  'conversation.citations': ConversationCitations;
  'conversation.semantic_search_members': ConversationSemanticSearchMembers;
  'conversation.semantic_searches': ConversationSemanticSearches;
  'identity.org_auth_config': IdentityOrgAuthConfig;
  'identity.org_logos': IdentityOrgLogos;
  'identity.orgs': IdentityOrgs;
  'identity.slug_counters': IdentitySlugCounters;
  'identity.used_reset_links': IdentityUsedResetLinks;
  'identity.user_activities': IdentityUserActivities;
  'identity.user_credentials': IdentityUserCredentials;
  'identity.user_group_members': IdentityUserGroupMembers;
  'identity.user_groups': IdentityUserGroups;
  'identity.user_pictures': IdentityUserPictures;
  'identity.users': IdentityUsers;
  'integrations.slack_threads': IntegrationsSlackThreads;
  'oauth.access_tokens': OauthAccessTokens;
  'oauth.agent_registrations': OauthAgentRegistrations;
  'oauth.apps': OauthApps;
  'oauth.authorization_codes': OauthAuthorizationCodes;
  'oauth.delegation_events': OauthDelegationEvents;
  'oauth.device_authorizations': OauthDeviceAuthorizations;
  'oauth.refresh_tokens': OauthRefreshTokens;
  'oauth.service_account_delegations': OauthServiceAccountDelegations;
  'platform.connectors_config': PlatformConnectorsConfig;
  'platform.docstore_changes': PlatformDocstoreChanges;
  'platform.mail_log': PlatformMailLog;
  'platform.notifications': PlatformNotifications;
  'platform.outbox': PlatformOutbox;
  'platform.store_epoch': PlatformStoreEpoch;
}
