-- docstore-migrate copies the Slack account links (integrations_0003) with the import login, as platform_data_0002 lets it copy
-- every other document-store table: the row policy for that role and the privileges a copy, a catch-up and a rollback need on this one table.
SELECT kernel.grant_import_access('integrations.slack_user_links');
GRANT SELECT, INSERT, UPDATE, DELETE ON integrations.slack_user_links TO pipeshub_import;
