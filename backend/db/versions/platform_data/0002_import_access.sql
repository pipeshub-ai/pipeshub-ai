-- The one-off copy of an existing install (docstore-migrate) writes with the import login, not with the jobs login the application
-- processes hold. The import role gets the row policy (kernel.grant_import_access) and the table privileges a copy, a catch-up and a
-- reset need on exactly the tables the copy writes, and nothing on the jobs or application roles changes. Global tables are written
-- through vetted import functions, which the role may call; the job state it saves is platform.job_checkpoint.
SELECT kernel.grant_import_access('identity.orgs');
SELECT kernel.grant_import_access('identity.org_logos');
SELECT kernel.grant_import_access('identity.users');
SELECT kernel.grant_import_access('identity.user_credentials');
SELECT kernel.grant_import_access('identity.user_groups');
SELECT kernel.grant_import_access('identity.user_group_members');
SELECT kernel.grant_import_access('identity.org_auth_config');
SELECT kernel.grant_import_access('identity.user_activities');
SELECT kernel.grant_import_access('identity.used_reset_links');
SELECT kernel.grant_import_access('identity.user_pictures');
SELECT kernel.grant_import_access('identity.slug_counters');
SELECT kernel.grant_import_access('conversation.chat_sessions');
SELECT kernel.grant_import_access('conversation.chat_session_members');
SELECT kernel.grant_import_access('conversation.chat_messages');
SELECT kernel.grant_import_access('conversation.semantic_searches');
SELECT kernel.grant_import_access('conversation.semantic_search_members');
SELECT kernel.grant_import_access('conversation.citations');
SELECT kernel.grant_import_access('content.documents');
SELECT kernel.grant_import_access('content.projects');
SELECT kernel.grant_import_access('content.project_members');
SELECT kernel.grant_import_access('platform.notifications');
SELECT kernel.grant_import_access('platform.mail_log');
SELECT kernel.grant_import_access('platform.connectors_config');
SELECT kernel.grant_import_access('platform.outbox');
SELECT kernel.grant_import_access('oauth.apps');
SELECT kernel.grant_import_access('oauth.access_tokens');
SELECT kernel.grant_import_access('oauth.refresh_tokens');
SELECT kernel.grant_import_access('oauth.authorization_codes');
SELECT kernel.grant_import_access('oauth.device_authorizations');
SELECT kernel.grant_import_access('oauth.service_account_delegations');
SELECT kernel.grant_import_access('oauth.delegation_events');
SELECT kernel.grant_import_access('integrations.slack_threads');

GRANT USAGE ON SCHEMA identity, conversation, content, platform, oauth, integrations TO pipeshub_import;
GRANT SELECT, INSERT, UPDATE, DELETE ON identity.orgs, identity.org_logos, identity.users, identity.user_credentials, identity.user_groups, identity.user_group_members, identity.org_auth_config, identity.user_activities, identity.used_reset_links, identity.user_pictures, identity.slug_counters, conversation.chat_sessions, conversation.chat_session_members, conversation.chat_messages, conversation.semantic_searches, conversation.semantic_search_members, conversation.citations, content.documents, content.projects, content.project_members, platform.notifications, platform.mail_log, platform.connectors_config, platform.outbox, oauth.apps, oauth.access_tokens, oauth.refresh_tokens, oauth.authorization_codes, oauth.device_authorizations, oauth.service_account_delegations, oauth.delegation_events, integrations.slack_threads TO pipeshub_import;

-- The function belongs to its schema's vetted owner; the grant is made as the owner, the way the revisions that create them do.
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE;
SET LOCAL ROLE pipeshub_vetted_platform;
GRANT EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) TO pipeshub_import;
SET LOCAL ROLE pipeshub_owner;
REVOKE pipeshub_vetted_platform FROM pipeshub_owner;
