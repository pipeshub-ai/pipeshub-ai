import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { SharedChatEmptyState } from '../shared-chat-empty-state';

afterEach(cleanup);

describe('SharedChatEmptyState', () => {
  it('offers two sendable @assistant prompts, a mention action with the note helper text, and the help hint', () => {
    render(<Theme><SharedChatEmptyState onPick={vi.fn()} onStartNote={vi.fn()} /></Theme>);
    expect(screen.getAllByRole('button')).toHaveLength(3);
    expect(screen.getByTestId('shared-chat-empty-note-example').textContent).toContain('@teammate');
    expect(screen.queryByRole('button', { name: /@teammate/ })).toBeNull();
    expect(screen.getByRole('button', { name: "@assistant summarize what we've decided so far" })).toBeTruthy();
    expect(screen.getByText(/@assistant help/).textContent).toContain('what the assistant can access for you');
  });

  it('sends the prompt that was picked', () => {
    const onPick = vi.fn();
    render(<Theme><SharedChatEmptyState onPick={onPick} onStartNote={vi.fn()} /></Theme>);
    fireEvent.click(screen.getByRole('button', { name: '@assistant what are the open questions?' }));
    expect(onPick).toHaveBeenCalledExactlyOnceWith('@assistant what are the open questions?');
  });

  it('the mention action starts a mention in the composer and sends nothing', () => {
    const onPick = vi.fn();
    const onStartNote = vi.fn();
    render(<Theme><SharedChatEmptyState onPick={onPick} onStartNote={onStartNote} /></Theme>);
    fireEvent.click(screen.getByTestId('shared-chat-empty-mention'));
    expect(onStartNote).toHaveBeenCalledTimes(1);
    expect(onPick).not.toHaveBeenCalled();
  });
});
