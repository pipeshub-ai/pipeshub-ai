-- pc_top: a record that has no PARENT_CHILD parent that is a record (kg.model.record_row), written by w_pc_top_set. A knowledge base's root lists exactly such records of it,
-- so its listing reads them by records_pc_tops instead of every record of the knowledge base.
ALTER TABLE kg.records ADD COLUMN pc_top boolean NOT NULL DEFAULT false;
