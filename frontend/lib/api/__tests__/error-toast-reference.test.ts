import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ErrorType, type ProcessedError } from '../api-error';

const errorToast = vi.fn(() => 'toast-1');

vi.mock('@/lib/store/toast-store', () => ({
  toast: { error: (...args: unknown[]) => errorToast(...args) },
  useToastStore: { getState: () => ({ toasts: [] }) },
}));

import { showErrorToast } from '../error-toast';

beforeEach(() => {
  errorToast.mockClear();
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
