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
