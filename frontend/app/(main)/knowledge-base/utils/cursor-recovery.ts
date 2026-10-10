import { isProcessedError } from '@/lib/api/api-error';

/**
 * Was this failure the API refusing the cursor we sent?
 *
 * A cursor can be refused for reasons the user cannot act on: it was signed
 * with a key that has since rotated, issued to a different user or org, or
 * edited in a URL someone shared. Such a page returns to the first page rather
 * than stranding the view on an error.
 *
 * Only a 400 qualifies — a rejected cursor is a malformed request, whereas
 * 403/404/5xx are about the node or the server and must still surface. A 400
 * raised by something else (an unknown sort field, say) clears a cursor that
 * was not at fault; the refetch then reports that error with no cursor left to
 * retry, so this cannot loop.
 */
export function isRejectedCursorError(error: unknown, cursor: string | null): boolean {
  if (!cursor) return false;
  const status = isProcessedError(error)
    ? error.statusCode
    : ((error as { statusCode?: number; response?: { status?: number } })?.statusCode ??
      (error as { response?: { status?: number } })?.response?.status);
  return status === 400;
}

/** For `suppressErrorToast`: the page goes back to the first page by itself. */
export function quietRejectedCursor(cursor: string | null | undefined) {
  return (error: unknown) => isRejectedCursorError(error, cursor ?? null);
}
