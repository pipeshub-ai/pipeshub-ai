import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import type { ConversationMessage } from '@/chat/types';

const api = vi.hoisted(() => ({ requestAccess: vi.fn() }));
vi.mock('@/app/(main)/chat/api', () => ({ ChatApi: api }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { ArchivedChatView } from '../components/archived-chat-view';

installBrowserShims();
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('an archived chat asks for a hidden source in the chat it is showing', () => {
  it('sends the conversation it was given, not whichever chat is active elsewhere', async () => {
    api.requestAccess.mockResolvedValue({});
    const messages = [
      { _id: 'q1', messageType: 'user_query', content: 'What about it?' },
      {
        _id: 'a1',
        messageType: 'bot_response',
        content: 'Part of it.',
        redaction: { redacted: false, partial: true, reason: 'no_access' },
        citations: [
          { citationId: 'c1', redacted: true, reason: 'no_access', requestAccess: { recordId: 'r1', kind: 'native', endpoint: '/api/v1/access-requests' } },
        ],
      },
    ] as unknown as ConversationMessage[];

    renderInTheme(<ArchivedChatView conversationId="archived-7" conversationTitle="T" messages={messages} isLoading={false} error={null} />);
    fireEvent.click(screen.getByRole('button', { name: /request access/i }));

    await waitFor(() => expect(api.requestAccess).toHaveBeenCalledWith('r1', { conversationId: 'archived-7' }));
  });
});
