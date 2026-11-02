import React, { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { TagInput, type TagItem } from '../tag-input';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

afterEach(() => cleanup());

const tag = (value: string): TagItem => ({ id: value, value });

/** A parent that keeps only the values, as the approval form does. */
function ValuesOnly({ initial, freeText = true }: { initial: string[]; freeText?: boolean }) {
  const [values, setValues] = useState(initial);
  return (
    <TagInput id="tags" freeText={freeText} tags={values.map(tag)} onTagsChange={(tags) => setValues(tags.map((t) => t.value))} />
  );
}

const input = () => document.getElementById('tags') as HTMLInputElement;
const chips = () => screen.queryAllByRole('button').map((b) => b.getAttribute('aria-label'));

describe('TagInput', () => {
  it('Tab is never held: with no text it does nothing, with text the text becomes a tag on leaving', () => {
    const onTagsChange = vi.fn();
    render(<Theme><TagInput id="tags" freeText tags={[]} onTagsChange={onTagsChange} /></Theme>);
    const empty = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    input().dispatchEvent(empty);
    expect(empty.defaultPrevented).toBe(false);

    fireEvent.change(input(), { target: { value: 'auth' } });
    const typed = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    input().dispatchEvent(typed);
    expect(typed.defaultPrevented).toBe(false);
    fireEvent.blur(input());
    expect(onTagsChange).toHaveBeenLastCalledWith([expect.objectContaining({ value: 'auth' })]);
  });

  it('Tab does not end a tag in the comma-separated mode either', () => {
    render(<Theme><TagInput id="tags" tags={[]} onTagsChange={vi.fn()} /></Theme>);
    const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    input().dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
  });

  it('Backspace twice removes the last tag, even when the parent keeps only values', () => {
    render(<Theme><ValuesOnly initial={['auth', 'web']} /></Theme>);
    fireEvent.keyDown(input(), { key: 'Backspace' });
    expect(chips()).toHaveLength(2);
    fireEvent.keyDown(input(), { key: 'Backspace' });
    expect(chips()).toEqual([expect.stringContaining('auth')]);
  });

  it('removing a tag with its × moves focus to the next ×, then to the input', () => {
    vi.useFakeTimers();
    try {
      render(<Theme><ValuesOnly initial={['auth', 'web']} /></Theme>);
      fireEvent.click(screen.getAllByRole('button')[0]);
      act(() => vi.runAllTimers());
      expect(document.activeElement?.getAttribute('aria-label')).toContain('web');
      fireEvent.click(screen.getAllByRole('button')[0]);
      act(() => vi.runAllTimers());
      expect(document.activeElement).toBe(input());
    } finally {
      vi.useRealTimers();
    }
  });

  it('puts the id on the text input, so a label names it', () => {
    render(<Theme><label htmlFor="tags">Tags</label><TagInput id="tags" tags={[]} onTagsChange={vi.fn()} /></Theme>);
    expect(screen.getByRole('textbox', { name: 'Tags' })).toBe(input());
  });
});
