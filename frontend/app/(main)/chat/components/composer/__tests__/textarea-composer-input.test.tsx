import React, { createRef } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { TextareaComposerInput } from '../textarea-composer-input';
import type { ComposerInputHandle, ComposerInputProps } from '../composer-input.types';

afterEach(cleanup);

function setup(props: Partial<ComposerInputProps> = {}) {
  const ref = createRef<ComposerInputHandle>();
  const onChange = vi.fn();
  const onKeyDown = vi.fn();
  const utils = render(
    <TextareaComposerInput
      ref={ref}
      value=""
      placeholder="Ask anything..."
      ariaLabel="Message"
      onChange={onChange}
      onKeyDown={onKeyDown}
      {...props}
    />,
  );
  return { ref, onChange, onKeyDown, ...utils };
}

const box = () => screen.getByTestId('chat-composer') as HTMLTextAreaElement;

describe('TextareaComposerInput', () => {
  it('is a labelled textbox with a stable test id', () => {
    setup();
    expect(screen.getByRole('textbox', { name: 'Message' })).toBe(box());
    expect(box().placeholder).toBe('Ask anything...');
  });

  it('reports typed text and key presses to the owner', () => {
    const { onChange, onKeyDown } = setup();
    fireEvent.change(box(), { target: { value: 'hi' } });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('hi');
    expect(onKeyDown).toHaveBeenCalledTimes(1);
  });

  it('shows interim speech after the text without committing it', () => {
    const { ref } = setup({ value: 'Tell me', interim: 'the plan' });
    expect(box().value).toBe('Tell me the plan');
    expect(ref.current?.getValue()).toEqual({ text: 'Tell me', mentions: [] });
    cleanup();
    setup({ value: '', interim: 'hello' });
    expect(box().value).toBe('hello');
  });

  it('is read-only and dimmed when asked', () => {
    setup({ readOnly: true });
    expect(box().readOnly).toBe(true);
    expect(box().style.color).toBe('var(--slate-a8)');
  });

  it('caps its height at maxHeightPx, 120 by default', () => {
    setup({ maxHeightPx: 90 });
    Object.defineProperty(box(), 'scrollHeight', { configurable: true, value: 300 });
    fireEvent.input(box());
    expect(box().style.height).toBe('90px');
    expect(box().style.maxHeight).toBe('90px');
    cleanup();
    setup();
    expect(box().style.maxHeight).toBe('120px');
  });

  it('handle: focus, clear, setText', () => {
    const { ref, onChange } = setup({ value: 'abc' });
    act(() => ref.current?.focus());
    expect(document.activeElement).toBe(box());

    box().style.height = '80px';
    act(() => ref.current?.clear());
    expect(onChange).toHaveBeenLastCalledWith('');
    expect(box().style.height).toBe('auto');

    act(() => ref.current?.setText('new'));
    expect(onChange).toHaveBeenLastCalledWith('new');
  });

  it('handle: insertMention puts the label in as plain text at the caret and carries no ids', () => {
    const { ref, onChange } = setup({ value: 'hi  there' });
    box().setSelectionRange(3, 3);
    act(() => ref.current?.insertMention({ type: 'user', id: 'u1' }, 'Bob'));
    expect(onChange).toHaveBeenLastCalledWith('hi @Bob  there');
    expect(ref.current?.getValue().mentions).toEqual([]);
  });
});
