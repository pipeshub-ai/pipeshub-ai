import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ErrorType, type ProcessedError } from '../api-error';

const errorToast = vi.fn(() => 'toast-docs');

vi.mock('@/lib/store/toast-store', () => ({
  toast: { error: (...args: unknown[]) => errorToast(...args) },
  useToastStore: { getState: () => ({ toasts: [] }) },
}));

import { showErrorToast, splitDocsLink } from '../error-toast';

const GUIDE = 'https://docs.pipeshub.com/connectors/confluence/confluence#real-time-updates';

beforeEach(() => errorToast.mockClear());

function badRequest(message: string): ProcessedError {
  return { type: ErrorType.VALIDATION_ERROR, message, statusCode: 400, requestId: 'ref-9' };
}

type ToastOptions = { description: string; action?: { label: string; href?: string; openInNewTab?: boolean } };

describe('a docs link at the end of a server message', () => {
  it('becomes the toast button and leaves the text short', () => {
    showErrorToast(badRequest(`The app is not installed on acme.atlassian.net. ${GUIDE}`));

    const [, options] = errorToast.mock.calls[0] as unknown as [string, ToastOptions];
    expect(options.description).toBe('The app is not installed on acme.atlassian.net. Reference: ref-9');
    expect(options.action).toMatchObject({ href: GUIDE, openInNewTab: true });
    expect(options.action?.label).toBeTruthy();
  });

  it('stays in the text when it is not a PipesHub docs link', () => {
    showErrorToast(badRequest('See https://example.com/help'));

    const [, options] = errorToast.mock.calls[0] as unknown as [string, ToastOptions];
    expect(options.description).toContain('https://example.com/help');
    expect(options.action).toBeUndefined();
  });

  it('adds no button to a message without a link', () => {
    showErrorToast(badRequest('Admin access required.'));

    const [, options] = errorToast.mock.calls[0] as unknown as [string, ToastOptions];
    expect(options.action).toBeUndefined();
  });
});

describe('splitDocsLink', () => {
  it('drops a closing full stop after the link', () => {
    expect(splitDocsLink(`Set it up. ${GUIDE}.`)).toEqual({ text: 'Set it up.', href: GUIDE });
  });

  it('keeps a link in the middle of the text', () => {
    const text = `Read ${GUIDE} first, then try again.`;
    expect(splitDocsLink(text)).toEqual({ text });
  });

  it('keeps a message that is only a link', () => {
    expect(splitDocsLink(GUIDE)).toEqual({ text: GUIDE });
  });
});
