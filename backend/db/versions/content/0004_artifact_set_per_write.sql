-- The index of one row per revision goes once the index of one row per stored write is built (the .py builds it first, so the table
-- is never without a uniqueness rule). It refuses a second write's row of a revision.
DROP INDEX content.documents_artifact_set;
