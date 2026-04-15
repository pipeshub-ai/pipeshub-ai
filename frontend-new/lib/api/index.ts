// Axios instance with interceptors
export { apiClient, default } from './axios-instance';

// Runtime API base URL
export { getApiBaseUrl, setApiBaseUrl, hasApiBaseUrl, hasStoredApiBaseUrl, clearApiBaseUrl, isTauri } from './base-url';

// SWR fetchers
export { axiosFetcher, publicFetcher, configuredFetcher } from './fetcher';

// Streaming utilities (native fetch for SSE)
export { streamRequest, createStreamController, streamSSERequest } from './streaming';
export type { StreamingOptions, SSEEvent, SSEStreamingOptions } from './streaming';

// Error handling
export { processError, ErrorType, isProcessedError } from './api-error';
export type { ProcessedError } from './api-error';
