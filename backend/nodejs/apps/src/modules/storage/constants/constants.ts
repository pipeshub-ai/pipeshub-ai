export const storageEtcdPaths = '/services/storage';
export const endpoint = '/services/endpoints';
export const maxFileSizeForPipesHubService = 0 * 1024 * 1024;

// Ceiling for a download signed-URL lifetime. 7 days matches the hard limit
// AWS S3 (SigV4) and GCS (V4) enforce; Azure SAS has no limit of its own, so
// this cap is what protects Azure-backed installs from long-lived links.
export const MAX_SIGNED_URL_TTL_SECONDS = 604800;

// Shown to the person uploading when the file could not be written to storage.
export const STORAGE_WRITE_FAILED_MESSAGE =
  "We couldn't save this file right now. Please try again in a moment; if it keeps failing, ask your admin to check the storage settings.";

// Shared-content handover after a connector delete (relocate + list routes).
export const RELOCATE_MAX_MOVES = 100;
export const CONNECTOR_VRID_PAGE_DEFAULT = 500;
export const CONNECTOR_VRID_PAGE_MAX = 1000;
// Upper bound on one missing-documents lookup, so one request is one bounded $in query.
export const MAX_MISSING_DOCUMENT_IDS = 500;

// Artifact sets: one document per content revision, many files under its root.
export const ARTIFACT_URL_TTL_SECONDS = 15 * 60;
export const ARTIFACT_MAX_FILES_PER_CALL = 1000;
export const ARTIFACT_FILE_MAX_BYTES = 100 * 1024 * 1024;
