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
