-- The node_parent mark of a grant (kg/grant_marks.py), written by w_grant_parent_set: the node's one hierarchy parent, for a live OPEN record of its connector with exactly one
-- hierarchy parent and one record group, so that a browse of the root reads the viewer's chain-tops from the grants alone. NULL (read the node's rows) until it is written.
ALTER TABLE kg.scope_members ADD COLUMN node_parent bigint;
DROP INDEX kg.scope_members_tops;
