import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { InlineRenameField } from '../inline-rename-field';

afterEach(cleanup);

function field(value = 'Q3 pricing review') {
  const onCommit = vi.fn();
  const onCancel = vi.fn();
  render(
    <Theme>
      <InlineRenameField value={value} onCommit={onCommit} onCancel={onCancel} />
    </Theme>,
  );
  return { input: screen.getByRole('textbox', { name: 'New name' }) as HTMLInputElement, onCommit, onCancel };
}

describe('InlineRenameField', () => {
  it('opens focused with the whole name selected, at its own width rather than the whole row', () => {
    const { input } = field();
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('Q3 pricing review'.length);
    const root = input.closest('.rt-TextFieldRoot') as HTMLElement;
    expect(root.className).toContain('rt-r-size-2');
    expect(root.style.flex).toBe('0 1 auto');
    expect(root.style.width).toBe('');
    expect(root.style.marginRight).toBe('var(--space-1)');
  });

  it('Enter commits the trimmed name once; the blur that follows does not commit again', () => {
    const { input, onCommit, onCancel } = field();
    fireEvent.change(input, { target: { value: '  Q4 blockers ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.blur(input);
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith('Q4 blockers');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('Escape cancels; an empty or unchanged name is a cancel; blur commits a change', () => {
    let f = field();
    fireEvent.change(f.input, { target: { value: 'Other' } });
    fireEvent.keyDown(f.input, { key: 'Escape' });
    fireEvent.blur(f.input);
    expect(f.onCancel).toHaveBeenCalledTimes(1);
    expect(f.onCommit).not.toHaveBeenCalled();
    cleanup();

    f = field();
    fireEvent.change(f.input, { target: { value: '   ' } });
    fireEvent.blur(f.input);
    expect(f.onCancel).toHaveBeenCalledTimes(1);
    cleanup();

    f = field();
    fireEvent.blur(f.input);
    expect(f.onCancel).toHaveBeenCalledTimes(1);
    expect(f.onCommit).not.toHaveBeenCalled();
    cleanup();

    f = field();
    fireEvent.change(f.input, { target: { value: 'Q4 blockers' } });
    fireEvent.blur(f.input);
    expect(f.onCommit).toHaveBeenCalledWith('Q4 blockers');
  });
});
