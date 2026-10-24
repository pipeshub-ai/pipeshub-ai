import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  usePathname: () => '/knowledge-base',
}));

type OnSend = (message: string, attachments?: unknown, nodes?: unknown, extra?: unknown) => Promise<unknown>;
const composer = vi.hoisted(() => ({ onSend: null as OnSend | null }));
vi.mock('@/chat/components/chat-input', () => ({
  ChatInput: ({ onSend }: { onSend: OnSend }) => {
    composer.onSend = onSend;
    return null;
  },
}));

import { ChatWidgetWrapper } from '../chat-widget-wrapper';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';

beforeEach(() => usePendingChatStore.setState({ pending: null }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("a collection's chat widget", () => {
  it('hands what its send carried besides the text to the chat page, so an @-mentioned agent still answers', async () => {
    render(<ChatWidgetWrapper currentTitle="Plans" selectedKbId="kb-1" isAllRecordsMode={false} />);
    const extra = { mentions: [{ type: 'agent', id: 'ag-1' }] };

    await composer.onSend!('<@agent:ag-1> summarise', undefined, undefined, extra);

    expect(routerPush).toHaveBeenCalledWith('/chat');
    expect(usePendingChatStore.getState().pending).toMatchObject({ message: '<@agent:ag-1> summarise', extra });
  });
});
