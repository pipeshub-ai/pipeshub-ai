import { describe, it, expect } from 'vitest';
import type { AxiosError } from 'axios';
import {
  ErrorType,
  isSearchNoAccessibleDocumentsNotFound,
  processError,
  SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS,
} from '../api-error';

const NO_DOCUMENTS_MESSAGE =
  'No documents are available for you to search yet. Upload files in Collections or connect a data source under Connectors so content can be indexed.';

/** Shape the Node search route answers with when retrieval reports an empty scope. */
function searchNoDocumentsBody(overrides: Record<string, unknown> = {}) {
  return {
    error: { code: 'HTTP_NOT_FOUND', message: NO_DOCUMENTS_MESSAGE },
    status: SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS,
    ...overrides,
  };
}

function axiosErrorWith(status: number, data: unknown): AxiosError {
  return { message: 'Request failed', response: { status, data } } as unknown as AxiosError;
}

describe('processError — search 404 with retrieval status', () => {
  it('keeps the backend message and lifts `status` into details.apiStatus', () => {
    const processed = processError(axiosErrorWith(404, searchNoDocumentsBody()));

    expect(processed.type).toBe(ErrorType.NOT_FOUND);
    expect(processed.statusCode).toBe(404);
    expect(processed.message).toBe(NO_DOCUMENTS_MESSAGE);
    expect(processed.details?.apiStatus).toBe(SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS);
  });
});

describe('isSearchNoAccessibleDocumentsNotFound', () => {
  it('is true for the empty-scope 404 the search route returns', () => {
    const processed = processError(axiosErrorWith(404, searchNoDocumentsBody()));
    expect(isSearchNoAccessibleDocumentsNotFound(processed)).toBe(true);
  });

  it('is false for a 404 that carries the same copy but no status', () => {
    const processed = processError(axiosErrorWith(404, searchNoDocumentsBody({ status: undefined })));
    expect(processed.message).toBe(NO_DOCUMENTS_MESSAGE);
    expect(isSearchNoAccessibleDocumentsNotFound(processed)).toBe(false);
  });

  it('is false for a 404 with a different retrieval status', () => {
    const processed = processError(
      axiosErrorWith(404, {
        error: { code: 'HTTP_NOT_FOUND', message: 'Knowledge base kb1 not found' },
        status: 'kb_not_found',
      }),
    );
    expect(isSearchNoAccessibleDocumentsNotFound(processed)).toBe(false);
  });

  it('is false when the status arrives on a non-404 response', () => {
    const processed = processError(axiosErrorWith(500, searchNoDocumentsBody()));
    expect(processed.type).toBe(ErrorType.SERVER_ERROR);
    expect(isSearchNoAccessibleDocumentsNotFound(processed)).toBe(false);
  });

  it('is false for errors that were never processed by the API client', () => {
    expect(isSearchNoAccessibleDocumentsNotFound(new Error(NO_DOCUMENTS_MESSAGE))).toBe(false);
    expect(isSearchNoAccessibleDocumentsNotFound(null)).toBe(false);
  });
});
