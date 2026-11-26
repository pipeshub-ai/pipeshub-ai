-- Only the document-store copy saves a running job's progress. The jobs login is held by every background process of the application; with
-- EXECUTE on platform.job_checkpoint any of them could overwrite the progress the copy resumes from. The copy saves it with the import login
-- (platform_data_0002), which logs in only while a move runs; the operator (the owner) keeps it, as for job_start and job_finish.
GRANT pipeshub_vetted_platform TO pipeshub_owner WITH SET TRUE;
SET LOCAL ROLE pipeshub_vetted_platform;
REVOKE EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) FROM pipeshub_jobs;
GRANT EXECUTE ON FUNCTION platform.job_checkpoint(bigint, text) TO pipeshub_owner;
SET LOCAL ROLE pipeshub_owner;
REVOKE pipeshub_vetted_platform FROM pipeshub_owner;
