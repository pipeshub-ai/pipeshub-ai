export const storageEtcdPaths = '/services/storage';
export const endpoint = '/services/endpoints';
export const maxFileSizeForPipesHubService = 0 * 1024 * 1024;

// Shown to the person uploading when the file could not be written to storage.
export const STORAGE_WRITE_FAILED_MESSAGE =
  "We couldn't save this file right now. Please try again in a moment; if it keeps failing, ask your admin to check the storage settings.";
