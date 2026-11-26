-- docstore-migrate's refusals (R48MIGFIX-FU-2): every document the copy left out, by collection and source id, with the importer's
-- reason. The job state lists the first 300 of a collection for the operator; verify and rollback read this complete list, so a refused
-- document never reads as missing and a rollback never removes one from MongoDB that PostgreSQL never held. A refused document may name
-- no organisation (that can be why it was refused), so the list is deployment-wide. Written by the import role in the unit that writes
-- the rows; nothing of the application reads it.
CREATE TABLE platform.docstore_refused (
  collection text COLLATE "C" NOT NULL,
  source_id text COLLATE "C" NOT NULL,
  reason text NOT NULL,
  PRIMARY KEY (collection, source_id)
);
SELECT kernel.register_internal('platform.docstore_refused');
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.docstore_refused TO pipeshub_import;
