/** What `apiClient` rejects with for a refused harness route: a ProcessedError carrying the response body. */
export function nodeRefusal(statusCode: number, code: string | null, message = 'Refused') {
  return {
    type: 'ERR',
    statusCode,
    message,
    originalError: {
      response: { status: statusCode, data: code === null ? { message } : { error: { code: `HTTP_AGENT_HARNESS_${code}`, message } } },
    },
  };
}

const NODE_CLASS: Readonly<Record<number, string>> = {
  400: 'HTTP_BAD_REQUEST',
  403: 'HTTP_FORBIDDEN',
  404: 'HTTP_NOT_FOUND',
  409: 'HTTP_CONFLICT',
  422: 'HTTP_UNPROCESSABLE_ENTITY',
  502: 'HTTP_BAD_GATEWAY',
  503: 'HTTP_SERVICE_UNAVAILABLE',
};

/**
 * A Python route's refusal as Node's error middleware relays it (H3): the HTTP
 * class in `code`, the route's machine code in `reasonCode`, its sentence (a
 * 4xx) or PipesHub's own (a 5xx) in `message`.
 */
export function relayedRefusal(statusCode: number, reasonCode: string, message = 'Refused') {
  const error = { code: NODE_CLASS[statusCode] ?? 'HTTP_INTERNAL_SERVER_ERROR', message, reasonCode };
  return { type: 'ERR', statusCode, message, originalError: { response: { status: statusCode, data: { error } } } };
}

/** Every key a copy table names, so a test can check each resolves. */
export const copyKeys = (...tables: Array<Readonly<Record<string, string>>>): string[] =>
  [...new Set(tables.flatMap((table) => Object.values(table)))];
