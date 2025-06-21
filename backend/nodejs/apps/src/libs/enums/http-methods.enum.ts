export const HttpMethod = Object.freeze({
  GET: 'GET',
  POST: 'POST',
  PUT: 'PUT',
  DELETE: 'DELETE',
  PATCH: 'PATCH',
  HEAD: 'HEAD',
  OPTIONS: 'OPTIONS',
  CONNECT: 'CONNECT',
  TRACE: 'TRACE',
} as const);

// Create a type for the HttpMethod keys
export type HttpMethod = (typeof HttpMethod)[keyof typeof HttpMethod];
