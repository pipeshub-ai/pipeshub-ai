import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { TextPreviewDialog } from '../../components/text-preview-dialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? _key,
  }),
}));

// jsdom has no matchMedia; useIsMobile needs it to resolve the touch-target size.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }),
});

afterEach(() => cleanup());

const h = React.createElement;

function renderDialog(props: Partial<React.ComponentProps<typeof TextPreviewDialog>> = {}) {
  const onOpenChange = vi.fn();
  const defaultProps: React.ComponentProps<typeof TextPreviewDialog> = {
    open: true,
    onOpenChange,
    title: 'pasted-text-2026-01-01-00-00-00.txt',
    loadText: vi.fn().mockResolvedValue('line one\nline two\nline three'),
    ...props,
  };
  const utils = render(h(Theme, null, h(TextPreviewDialog, defaultProps)));
  return { ...utils, onOpenChange, defaultProps };
}

describe('TextPreviewDialog', () => {
  it('shows a loading state before loadText resolves', () => {
    let resolvePromise: (v: string) => void = () => {};
    const loadText = vi.fn(() => new Promise<string>((resolve) => { resolvePromise = resolve; }));
    renderDialog({ loadText });
    expect(screen.getByText('Loading…')).toBeTruthy();
    resolvePromise('done');
  });

  it('renders the title and loaded content', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    // Title appears twice (visually-hidden Dialog.Title + visible header label).
    expect(screen.getAllByText('pasted-text-2026-01-01-00-00-00.txt').length).toBeGreaterThan(0);
  });

  it('renders an error message when loadText rejects', async () => {
    const loadText = vi.fn().mockRejectedValue(new Error('boom'));
    renderDialog({ loadText });
    await waitFor(() => expect(screen.getByText('boom')).toBeTruthy());
  });

  it('does not call loadText when closed', () => {
    const loadText = vi.fn().mockResolvedValue('content');
    renderDialog({ open: false, loadText });
    expect(loadText).not.toHaveBeenCalled();
  });

  it('starts a fresh load when reopened after closing mid-load', async () => {
    const loadText = vi
      .fn()
      .mockImplementationOnce(() => new Promise<string>(() => {}))
      .mockImplementationOnce(() => Promise.resolve('second load'));
    const props = { open: true, onOpenChange: vi.fn(), title: 'pasted.txt', loadText };
    const { rerender } = render(h(Theme, null, h(TextPreviewDialog, props)));
    rerender(h(Theme, null, h(TextPreviewDialog, { ...props, open: false })));
    rerender(h(Theme, null, h(TextPreviewDialog, props)));
    await waitFor(() => expect(screen.getByText('second load', { exact: false })).toBeTruthy());
    expect(loadText).toHaveBeenCalledTimes(2);
  });

  it('copies the loaded text when the copy button is clicked', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    renderDialog();
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith('line one\nline two\nline three');
  });

  it('schedules the feedback reset only once the copy settles', async () => {
    let settle: () => void = () => {};
    const writeText = vi.fn(() => new Promise<void>((resolve) => { settle = resolve; }));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const timers = vi.spyOn(globalThis, 'setTimeout');
    const feedbackTimers = () => timers.mock.calls.filter(([, ms]) => ms === 2000).length;

    renderDialog();
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /^copy$/i }));
    expect(feedbackTimers()).toBe(0);

    settle();
    await waitFor(() => expect(feedbackTimers()).toBe(1));
    timers.mockRestore();
  });

  it('shows the failure state instead of throwing when clipboard is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    renderDialog();
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    const copy = screen.getByRole('button', { name: /^copy$/i });
    expect(() => fireEvent.click(copy)).not.toThrow();
    await waitFor(() => expect(copy.textContent).toContain('close'));
  });

  it('calls onShowInTextField with the loaded text and closes the dialog', async () => {
    const onShowInTextField = vi.fn();
    const { onOpenChange } = renderDialog({ onShowInTextField });
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /show in text field/i }));
    expect(onShowInTextField).toHaveBeenCalledWith('line one\nline two\nline three');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not render the "Show in text field" action when the handler is omitted', async () => {
    renderDialog({ onShowInTextField: undefined });
    await waitFor(() => expect(screen.getByText('line one', { exact: false })).toBeTruthy());
    expect(screen.queryByRole('button', { name: /show in text field/i })).toBeNull();
  });
});
