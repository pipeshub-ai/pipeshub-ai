-- The placement marks of a grant (kg/grant_marks.py), written by w_grant_marks_set. parent_held: the same principal holds, with the same type, a grant on a live OPEN hierarchy
-- parent of the node in its connector, so whoever holds this grant may open that parent and the node is no chain-top of theirs. node_group: the node's one record group (NULL
-- when it is a group, or has none or several). Both have the default that lists every grant as it was: a mark reads as "may be a chain-top" until it is written.
ALTER TABLE kg.scope_members ADD COLUMN parent_held boolean NOT NULL DEFAULT false;
ALTER TABLE kg.scope_members ADD COLUMN node_group bigint;
