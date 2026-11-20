-- The label of an answer row: what the model calls behind it were given (provenance), the version of the stored content it
-- belongs to (label_ver), and the labels of the row's earlier versions (label_history), each stored whole.
-- The repository never names provenance or label_history in a statement that returns rows, so no read hands them to a caller.
-- Nullable or with a constant default: existing rows read as written before labels existed, and an older build's inserts and
-- updates keep working (an update that changes content without a new label leaves the hash unmatched, so the row reads as
-- its asker's alone).
ALTER TABLE conversation.chat_messages
  ADD COLUMN label_ver text,
  ADD COLUMN provenance jsonb,
  ADD COLUMN label_history jsonb NOT NULL DEFAULT '[]';
