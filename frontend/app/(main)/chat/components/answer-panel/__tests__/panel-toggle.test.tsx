import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

let belowDesktop = false;
vi.mock('@/lib/hooks/use-is-mobile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/hooks/use-is-mobile')>()),
  useIsBelowDesktop: () => belowDesktop,
}));

import { useChatStore } from '../../../store';
import { useAnswerPanelStore } from '../answer-panel-store';
import { ContextButton, PanelClose } from '../panel-toggle';

const initialChatState = useChatStore.getState();
let slotId = '';
beforeEach(() => {
  belowDesktop = false;
  useChatStore.setState(initialChatState, true);
  useAnswerPanelStore.setState({ panelOpen: undefined });
  slotId = useChatStore.getState().createSlot('conv-1');
});
afterEach(() => cleanup());

const ask = () => act(() => useChatStore.getState().updateSlot(slotId, { messages: [{ id: 'q1', role: 'user', content: [{ type: 'text', text: 'What is open?' }] }] }));
const opener = () => screen.queryByRole('button', { name: 'Context' });

describe('ContextButton', () => {
  it('stays in the bar open or shut, pressed while open; pressing it again closes the column', () => {
    ask();
    render(<Theme><ContextButton slotId={slotId} /></Theme>);
    expect(opener()?.getAttribute('aria-pressed')).toBe('false');
    expect(opener()!.textContent).toContain('stacks');
    fireEvent.click(opener()!);
    expect(useAnswerPanelStore.getState().panelOpen).toBe(true);
    expect(opener()?.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(opener()!);
    expect(useAnswerPanelStore.getState().panelOpen).toBe(false);
    expect(opener()?.getAttribute('aria-pressed')).toBe('false');
  });

  it('starts shut, waits for a question, and is absent below 1024 px', () => {
    const { rerender } = render(<Theme><ContextButton slotId={slotId} /></Theme>);
    expect(opener()).toBeNull();
    ask();
    expect(opener()?.getAttribute('aria-pressed')).toBe('false');
    belowDesktop = true;
    rerender(<Theme><ContextButton slotId={slotId} /></Theme>);
    expect(opener()).toBeNull();
  });
});

describe('PanelClose', () => {
  it('is a cross named Close context that shuts the column', () => {
    useAnswerPanelStore.setState({ panelOpen: true });
    render(<Theme><PanelClose /></Theme>);
    const close = screen.getByRole('button', { name: 'Close context' });
    expect(close.textContent).toContain('close');
    fireEvent.click(close);
    expect(useAnswerPanelStore.getState().panelOpen).toBe(false);
  });
});
