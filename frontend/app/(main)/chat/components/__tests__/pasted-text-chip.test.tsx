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
  initReactI18next: { type: '3rdParty', init: () => {} },
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
  onRemove: () => void;
  onRetry: () => void;
}> = {}) {
  const file = makeFile(fileOverrides);
  const props = {
    file,
    onPreview: vi.fn(),
    onRemove: vi.fn(),
    onRetry: vi.fn(),
    ...handlers,
  };
  const utils = render(h(Theme, null, h(PastedTextChip, props)));
  return { ...utils, props, file };
}

const chip = () => screen.getByRole('button', { name: /^Pasted text,/ }) as HTMLButtonElement;

describe('PastedTextChip', () => {
  it('is one 28px chip: the "Pasted text" label and its size, lines first, with no excerpt', () => {
    renderChip();
    expect(screen.getByText('Pasted text')).toBeTruthy();
    expect(screen.getByText('42 lines · 1.2k chars')).toBeTruthy();
    expect(screen.queryByText('First line of the pasted content')).toBeNull();
    expect(chip().style.height).toBe('calc(var(--space-5) + var(--space-1))');
    expect(chip().style.border).not.toContain('dashed');
  });

  it('uses the singular form for a one-line paste', () => {
    renderChip({ pasteCharCount: 50, pasteLineCount: 1 });
    expect(screen.getByText('1 line · 50 chars')).toBeTruthy();
  });

  it('is named for what it is and what it does', () => {
    renderChip();
    expect(chip().getAttribute('aria-label')).toBe('Pasted text, 42 lines · 1.2k chars. Open to read.');
  });

  it('shows a spinner while uploading, and can still be removed', () => {
    renderChip({ status: 'uploading' });
    expect(screen.getByRole('status')).toBeTruthy();
    expect(screen.getByRole('button', { name: /remove pasted text/i })).toBeTruthy();
  });

  it('shows the error message and a retry button on error', () => {
    renderChip({ status: 'error', errorMessage: 'Network error' });
    expect(screen.getByText('Network error')).toBeTruthy();
    expect(screen.getByRole('button', { name: /retry upload/i })).toBeTruthy();
  });

  it('gives the retry button a 24px box', () => {
    renderChip({ status: 'error', errorMessage: 'oops' });
    const retry = screen.getByRole('button', { name: /retry upload/i });
    expect(retry.style.width).toBe('var(--space-5)');
    expect(retry.style.height).toBe('var(--space-5)');
    expect(retry.className).toContain('touch-hit');
  });

  it('calls onRetry when the retry button is clicked', () => {
    const { props } = renderChip({ status: 'error', errorMessage: 'oops' });
    fireEvent.click(screen.getByRole('button', { name: /retry upload/i }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it('calls onRemove from the corner remove button, which is always in the DOM', () => {
    const { props } = renderChip();
    const remove = screen.getByRole('button', { name: /remove pasted text/i });
    fireEvent.click(remove);
    expect(props.onRemove).toHaveBeenCalledTimes(1);
    expect(props.onPreview).not.toHaveBeenCalled();
  });

  it('shows the remove button while the chip has keyboard focus', () => {
    renderChip();
    const remove = screen.getByRole('button', { name: /remove pasted text/i });
    expect(remove.style.opacity).toBe('0');
    fireEvent.focus(chip());
    expect(remove.style.opacity).toBe('1');
  });

  it('opens the reader on click', () => {
    const { props } = renderChip();
    fireEvent.click(chip());
    expect(props.onPreview).toHaveBeenCalledTimes(1);
  });

  it('is a focusable button, disabled on error', () => {
    renderChip();
    chip().focus();
    expect(document.activeElement).toBe(chip());

    cleanup();
    renderChip({ status: 'error', errorMessage: 'Upload failed' });
    expect(chip().disabled).toBe(true);
  });

  it('has no "Show in text field" action: that lives in the reader', () => {
    renderChip({ status: 'uploaded' });
    expect(screen.queryByRole('button', { name: /show in text field/i })).toBeNull();
  });
});
