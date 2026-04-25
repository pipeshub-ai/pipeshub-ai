// Axios instance with interceptors
export { apiClient, default } from './axios-instance';

// SWR fetchers
export { axiosFetcher, publicFetcher, configuredFetcher } from './fetcher';

// Streaming utilities (native fetch for SSE)
export { streamRequest, createStreamController, streamSSERequest } from './streaming';
export type { StreamingOptions, SSEEvent, SSEStreamingOptions } from './streaming';

// Error handling
export {
  processError,
  ErrorType,
  isProcessedError,
  isRequestCancelledError,
  isSearchNoAccessibleDocumentsNotFound,
  SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS,
  SEARCH_NO_ACCESSIBLE_DOCUMENTS_FRAGMENT,
} from './api-error';
export type { ProcessedError } from './api-error';

// Mutation helpers (loading state + toast integration)
export { useMutation } from './use-mutation';
export type {
  UseMutationResult,
  UseMutationRunOptions,
  UseMutationToastOptions,
} from './use-mutation';
export { withToast } from './with-toast';
export type { WithToastOptions } from './with-toast';
