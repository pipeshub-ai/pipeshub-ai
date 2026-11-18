-- Which rules an app's owner fields follow: written as 1 on every app the API stores now, and on each instance app its start-up
-- repair fixes. NULL is an app an older release stored (a dynamically registered client or the first-party device app may name a
-- borrowed user as its creator), which is what the repair looks for. No default: an older release's inserts must stay NULL.
ALTER TABLE oauth.apps ADD COLUMN owner_format smallint;
