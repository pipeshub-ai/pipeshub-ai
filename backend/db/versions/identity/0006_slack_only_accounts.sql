-- A Slack-only account: a person a Slack bot created an account for the first time they wrote to it. It can chat with that bot's
-- service agent from Slack and nothing else until an admin invites it, which turns the same row into kind 'human'.
-- The new check admits every existing row, so it is added NOT VALID and validated in place without a rewrite.
ALTER TABLE identity.users
  DROP CONSTRAINT users_kind_check,
  ADD CONSTRAINT users_kind_check CHECK (kind IN ('human', 'service', 'slack')) NOT VALID,
  ADD CONSTRAINT users_slack_is_not_admin CHECK (NOT (kind = 'slack' AND role = 'admin')) NOT VALID,
  ADD COLUMN slack_user_id text,
  ADD COLUMN slack_team_id text;
ALTER TABLE identity.users VALIDATE CONSTRAINT users_kind_check;
ALTER TABLE identity.users VALIDATE CONSTRAINT users_slack_is_not_admin;

-- Free text about an activity, such as which Slack bot created an account.
ALTER TABLE identity.user_activities ADD COLUMN details text;
