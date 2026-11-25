-- ADR-UI-01 D1 (UI-B1): a person's runs and jobs across their conversations, newest first, ties by run id so a page
-- cursor is exact; and one agent's runs of that person. The indexes are built in the .py (ensure_index): the table
-- exists before this revision.
SELECT 1;
