// Express matches routes case-insensitively, so /API/v1/... reaches the same
// handlers as /api/v1/...; anything that singles out API traffic must too.
export const isApiPath = (path: string): boolean =>
  path.toLowerCase().startsWith('/api/');

// Service-to-service routes (Python services calling Node) sit under an
// `internal` path segment; background sweeps hit them hundreds of times a minute.
export const isInternalApiPath = (path: string): boolean =>
  isApiPath(path) && path.toLowerCase().split('/').includes('internal');

// Successful internal calls drop to debug so they do not bury user traffic;
// an internal failure still logs at info.
export const accessLogLevel = (
  path: string,
  statusCode: number,
): 'info' | 'debug' | null => {
  if (!isApiPath(path)) return null;
  return isInternalApiPath(path) && statusCode < 400 ? 'debug' : 'info';
};
