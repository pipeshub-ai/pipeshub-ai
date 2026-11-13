-- name_sort: the listing's name key as the code units of UTF-16 (big endian), so that a bytewise order is the order the listing is merged and paged in (a string by UTF-16 code unit, not by code point).
ALTER TABLE kg.records ADD COLUMN name_sort bytea;
