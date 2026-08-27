import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { PastedTextChip } from '../pasted-text-chip';
import type { UploadedFile } from '../../types';

// Tests run without an initialized i18next instance — fall back to each call's
// `defaultValue` so assertions don't depend on locale resource files, applying
// the same plural selection and {{var}} interpolation i18next would.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (!opts) return key;
      const plural = opts.count === 1 ? (opts.defaultValue_one as string | undefined) : undefined;
      const template = plural ?? (opts.defaultValue as string | undefined);
      if (template === undefined) return key;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) =>
        opts[name] === undefined ? `{{${name}}}` : String(opts[name]),
      );
    },
  }),
}));

afterEach(() => cleanup());

const h = React.createElement;

function makeFile(overrides: Partial<UploadedFile> = {}): UploadedFile {
  return {
    id: 'file-1',
    file: new File(['pasted content'], 'pasted-text-2026-01-01-00-00-00.txt', { type: 'text/plain' }),
    name: 'pasted-text-2026-01-01-00-00-00.txt',
    size: 14,
    type: 'text/plain',
    status: 'uploaded',
    source: 'paste-text',
    pastePreview: 'First line of the pasted content',
    pasteCharCount: 1234,
    pasteLineCount: 42,
    ...overrides,
  };
}

function renderChip(fileOverrides: Partial<UploadedFile> = {}, handlers: Partial<{
  onPreview: () => void;
  onShowInTextField: () => void;
  onRemove: () => void;
  onRetry: () => void;
}> = {}) {
  const file = makeFile(fileOverrides);
  const props = {
    file,
    onPreview: vi.fn(),
    onShowInTextField: vi.fn(),
    onRemove: vi.fn(),
    onRetry: vi.fn(),
    ...handlers,
  };
  const utils = render(h(Theme, null, h(PastedTextChip, props)));
  return { ...utils, props, file };
}

describe('PastedTextChip', () => {
  it('renders the "Pasted text" label and excerpt', () => {
    renderChip();
    expect(screen.getByText('Pasted text')).toBeTruthy();
    expect(screen.getByText('First line of the pasted content')).toBeTruthy();
  });

  it('renders the char/line count meta label when uploaded', () => {
    renderChip();
    expect(screen.getByText('1.2k chars · 42 lines')).toBeTruthy();
  });

  it('uses the singular form for a one-line paste', () => {
    renderChip({ pasteCharCount: 50, pasteLineCount: 1 });
    expect(screen.getByText('50 chars · 1 line')).toBeTruthy();
  });

  it('shows a spinner and no remove button while uploading', () => {
    renderChip({ status: 'uploading' });
    expect(screen.queryByRole('button', { name: /remove pasted text/i })).toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('shows the error message and a retry button on error', () => {
    renderChip({ status: 'error', errorMessage: 'Network error' });
    expect(screen.getByText('Network error')).toBeTruthy();
    expect(screen.getByRole('button', { name: /retry upload/i })).toBeTruthy();
  });

  it('calls onRetry when the retry button is clicked', () => {
    const { props } = renderChip({ status: 'error', errorMessage: 'oops' });
    fireEvent.click(screen.getByRole('button', { name: /retry upload/i }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it('calls onRemove when the remove button is clicked', () => {
    const { props } = renderChip();
    fireEvent.click(screen.getByRole('button', { name: /remove pasted text/i }));
    expect(props.onRemove).toHaveBeenCalledTimes(1);
  });

  it('calls onPreview when the excerpt area is clicked (uploaded)', () => {
    const { props } = renderChip();
    fireEvent.click(screen.getByText('First line of the pasted content'));
    expect(props.onPreview).toHaveBeenCalledTimes(1);
  });

  it('shows "Show in text field" only once uploaded, and calls the handler on click', () => {
    const { props } = renderChip({ status: 'uploaded' });
    const action = screen.getByRole('button', { name: /show in text field/i });
    fireEvent.click(action);
    expect(props.onShowInTextField).toHaveBeenCalledTimes(1);
  });

  it('exposes the excerpt area as a focusable button, disabled on error', () => {
    renderChip();
    const preview = screen.getByText('First line of the pasted content').closest('button');
    expect(preview).not.toBeNull();
    preview!.focus();
    expect(document.activeElement).toBe(preview);

    cleanup();
    renderChip({ status: 'error', errorMessage: 'Upload failed' });
    expect(screen.getByText('First line of the pasted content').closest('button')!.disabled).toBe(true);
  });

  it('does not show "Show in text field" while uploading', () => {
    renderChip({ status: 'uploading' });
    expect(screen.queryByRole('button', { name: /show in text field/i })).toBeNull();
  });
});
