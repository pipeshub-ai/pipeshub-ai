// Express matches routes case-insensitively, so /API/v1/... reaches the same
// handlers as /api/v1/...; anything that singles out API traffic must too.
export const isApiPath = (path: string): boolean =>
  path.toLowerCase().startsWith('/api/');

// Service-to-service routes (Python services calling Node) sit under an
// `internal` path segment; background sweeps hit them hundreds of times a minute.
export const isInternalApiPath = (path: string): boolean =>
  isApiPath(path) && path.toLowerCase().split('/').includes('internal');

// Polled by the container healthcheck and the process monitor every few seconds.
const isHealthPath = (path: string): boolean => {
  const lower = path.toLowerCase();
  return lower === '/api/v1/health' || lower.startsWith('/api/v1/health/');
};

// The access line's level. Successful internal calls and health polls drop to
// debug so they do not bury user traffic. A 5xx is a warning: the error
// middleware's own line says why, at ERROR when the fault is ours.
export const accessLogLevel = (
  path: string,
  statusCode: number,
): 'warn' | 'info' | 'debug' | null => {
  if (!isApiPath(path)) return null;
  if (statusCode >= 500) return 'warn';
  if (statusCode < 400 && (isInternalApiPath(path) || isHealthPath(path))) {
    return 'debug';
  }
  return 'info';
};
