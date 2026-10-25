import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ErrorType, type ProcessedError } from '../api-error';

const errorToast = vi.fn(() => 'toast-1');
const openToasts: Array<{ id: string; isExiting: boolean }> = [];

vi.mock('@/lib/store/toast-store', () => ({
  toast: { error: (...args: unknown[]) => errorToast(...args) },
  useToastStore: { getState: () => ({ toasts: openToasts }) },
}));

import { showErrorToast } from '../error-toast';

beforeEach(() => {
  errorToast.mockClear();
  openToasts.length = 0;
});

function serverError(overrides: Partial<ProcessedError> = {}): ProcessedError {
  return {
    type: ErrorType.SERVER_ERROR,
    message:
      "Something went wrong on PipesHub's side. Please try again; if it keeps happening, ask your admin for help.",
    statusCode: 500,
    ...overrides,
  };
}

describe('the reference an admin can quote', () => {
  it('is shown beside the message, not buried in it', () => {
    showErrorToast(serverError({ requestId: '65f1c2ab9e4d7a3b1c0d8e2f-AbC123' }));

    const [, options] = errorToast.mock.calls[0] as [string, { description: string }];
    expect(options.description).toContain("went wrong on PipesHub's side");
    expect(options.description).toContain('Reference: 65f1c2ab9e4d7a3b1c0d8e2f-AbC123');
  });

  it('is left out when the server sent none', () => {
    showErrorToast(serverError());

    const [, options] = errorToast.mock.calls[0] as [string, { description: string }];
    expect(options.description).not.toContain('Reference:');
  });
});

describe('a server message without a full stop', () => {
  it('becomes a sentence before the reference', () => {
    showErrorToast(serverError({ message: 'admin access required', statusCode: 403, requestId: 'ref-1' }));

    const [, options] = errorToast.mock.calls[0] as [string, { description: string }];
    expect(options.description).toBe('Admin access required. Reference: ref-1');
  });

  it.each(['Still loading…', 'Saved (partly)', 'Name taken: "Ops"', 'Name taken: “Ops”', "Name taken: 'Ops'"])(
    'adds no full stop after an ending the server already closed: %s',
    (message) => {
      showErrorToast(serverError({ message, statusCode: 403, requestId: 'ref-3' }));

      const [, options] = errorToast.mock.calls[0] as [string, { description: string }];
      expect(options.description).toBe(`${message} Reference: ref-3`);
    },
  );

  it('keeps punctuation the server already wrote', () => {
    showErrorToast(serverError({ message: 'Try again later!', statusCode: 403, requestId: 'ref-2' }));

    const [, options] = errorToast.mock.calls[0] as [string, { description: string }];
    expect(options.description).toBe('Try again later! Reference: ref-2');
  });
});

describe('the toast title', () => {
  it('is a translated sentence-case title', () => {
    showErrorToast(serverError());

    expect(errorToast.mock.calls[0][0]).toBe('Server error');
  });

  it('asks for a retry when the server is busy', () => {
    showErrorToast(serverError({ statusCode: 503 }));

    expect(errorToast.mock.calls[0][0]).toBe('Please try again shortly');
  });
});

describe('the toast title', () => {
  const titleOf = () => (errorToast.mock.calls[0] as [string])[0];

  it('blames a connected service, not PipesHub, for a 502 our API answered', () => {
    showErrorToast(serverError({ statusCode: 502, requestId: 'ref-502', message: 'The email wasn\'t sent.' }));
    expect(titleOf()).toBe('Problem with a connected service');
  });

  it('stays "Server error" for a bare proxy 502', () => {
    showErrorToast(serverError({ statusCode: 502 }));
    expect(titleOf()).toBe('Server error');
  });
});

describe('an error toast that is already open', () => {
  const smtpFailure = (requestId: string) =>
    serverError({
      statusCode: 502,
      requestId,
      message: "The email wasn't sent because the mail (SMTP) server rejected the username or password.",
    });

  it('does not hide a different failure behind it', () => {
    errorToast.mockReturnValueOnce('generic').mockReturnValueOnce('smtp');
    showErrorToast(serverError({ requestId: 'ref-a' }));
    openToasts.push({ id: 'generic', isExiting: false });

    showErrorToast(smtpFailure('ref-b'));

    expect(errorToast).toHaveBeenCalledTimes(2);
    expect((errorToast.mock.calls[1] as [string])[0]).toBe('Problem with a connected service');
  });

  it('absorbs a repeat of the same failure, whatever its reference', () => {
    errorToast.mockReturnValueOnce('smtp-1');
    showErrorToast(smtpFailure('ref-c'));
    openToasts.push({ id: 'smtp-1', isExiting: false });

    showErrorToast(smtpFailure('ref-d'));

    expect(errorToast).toHaveBeenCalledTimes(1);
  });
});
