-- A Slack account a person linked, signed in, to their PipesHub account (ADR-SLACKID-01). One link per Slack account.
CREATE TABLE integrations.slack_user_links (
  org_id text NOT NULL,
  workspace_id text NOT NULL,
  slack_user_id text NOT NULL,
  team_id text NOT NULL,
  user_id text NOT NULL,
  email text NOT NULL,
  slack_name text NOT NULL DEFAULT '',
  linked_at timestamptz NOT NULL,
  version int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (org_id, workspace_id, slack_user_id)
);
CREATE INDEX slack_user_links_user ON integrations.slack_user_links (org_id, user_id);

SELECT kernel.apply_rls('integrations.slack_user_links', 'org');
GRANT SELECT, INSERT, UPDATE, DELETE ON integrations.slack_user_links TO pipeshub_node;
