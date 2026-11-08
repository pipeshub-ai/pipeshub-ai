import { ErrorType, type ProcessedError } from '@/lib/api';

/**
 * What the view does about a failed graph call. Hidden and missing nodes are
 * one answer on the server, so they are one state here; a failed check is
 * never an empty graph.
 */
export type GraphFailure =
  | 'unavailable' // 503, 5xx, network: retry, keep what is on screen
  | 'not-available' // 404: "This item isn't available."
  | 'revoked' // the view's focus is no longer available and nothing on the trail is either
  | 'full' // the view holds as many nodes as it may: open a node to start another
  | 'ambiguous' // 409: the repo name matches several; the server's words say which
  | 'bad-cursor' // 400 while paging: drop the cursor and expand again
  | 'refused' // 401/403: the app-wide handling owns it
  | 'unexpected'; // anything else: generic error, logged without ids

export function classifyGraphFailure(error: ProcessedError, pagingWithCursor = false): GraphFailure {
  const status = error?.statusCode;
  if (status === 404 || error?.type === ErrorType.NOT_FOUND) return 'not-available';
  if (status === 409) return 'ambiguous';
  if (status === 400 && pagingWithCursor) return 'bad-cursor';
  if (status === 401 || status === 403) return 'refused';
  if (
    status === undefined ||
    status >= 500 ||
    error?.type === ErrorType.NETWORK_ERROR ||
    error?.type === ErrorType.TIMEOUT_ERROR ||
    error?.type === ErrorType.SERVER_ERROR
  ) {
    return 'unavailable';
  }
  return 'unexpected';
}
