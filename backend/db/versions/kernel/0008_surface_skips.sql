-- Each migrate run that skipped the security surface comparison leaves a row; every later run reports them.
CREATE TABLE kernel.surface_skips (
  skipped_at timestamptz NOT NULL DEFAULT now(),
  skipped_by name NOT NULL DEFAULT session_user,
  files      text NOT NULL DEFAULT '');
