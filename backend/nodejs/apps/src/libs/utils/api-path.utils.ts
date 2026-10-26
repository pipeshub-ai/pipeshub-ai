// Express matches routes case-insensitively, so /API/v1/... reaches the same
// handlers as /api/v1/...; anything that singles out API traffic must too.
export const isApiPath = (path: string): boolean =>
  path.toLowerCase().startsWith('/api/');
