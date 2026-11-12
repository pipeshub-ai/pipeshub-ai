-- name_sort: the listing's name key as the code units of UTF-16 (big endian), so that a bytewise order is the order the listing is merged and paged in (a string by UTF-16 code unit, not by code point).
ALTER TABLE kg.records ADD COLUMN name_sort bytea;
-- The listing indexes: one connector's live listing rows in each sort order (the ordering and tie-break of the listing, in both directions), so that a window of a page is a range of an index.
-- Partial on exactly what a listing reads (live, no placeholder, not another organisation's), so that the predicate of the query implies it. A previous page reads the same index backwards.
CREATE INDEX records_list_name_asc ON kg.records (org_id, app_sk, name_sort ASC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_name_desc ON kg.records (org_id, app_sk, name_sort DESC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_updated_asc ON kg.records (org_id, app_sk, updated_ms ASC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_updated_desc ON kg.records (org_id, app_sk, updated_ms DESC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_created_asc ON kg.records (org_id, app_sk, created_ms ASC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_created_desc ON kg.records (org_id, app_sk, created_ms DESC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_size_asc ON kg.records (org_id, app_sk, size_bytes ASC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_size_desc ON kg.records (org_id, app_sk, size_bytes DESC NULLS LAST, (id COLLATE "C")) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;
CREATE INDEX records_list_class ON kg.records (org_id, app_sk, class_id, sk) WHERE kind IN (2, 3) AND NOT deleted AND NOT placeholder AND NOT alien;      -- the rows of one class of a connector: a count by what a viewer's grants open
CREATE INDEX records_hiders ON kg.records (org_id, app_sk) WHERE hide_children;      -- whether a connector has a group that hides its children: asked by every listing, never a scan of the connector
