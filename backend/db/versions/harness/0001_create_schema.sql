-- Schema for the agent harness's stores under DOC_STORE=postgres (runs, journal, ledgers, audit chain, approvals,
-- memories, schedules, workspaces, erasure jobs, the control plane). Tables arrive in later revisions of this branch.
--
-- The tables follow one shape: org_id leads every key, `id` is the stored document's key, `doc` is the document as the
-- harness's MongoDB store keeps it (dates as {"$date": ms}, bytes as {"$bin": base64}), and the fields a query filters
-- or sorts on are generated columns of `doc`, so the document stays the one source of truth and every index names a
-- typed column. `region` is the deployment region each row was written in (OWD-12).
CREATE SCHEMA IF NOT EXISTS harness;
