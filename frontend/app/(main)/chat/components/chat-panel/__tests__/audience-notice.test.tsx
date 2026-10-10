/**
 * The composer's disclosure in a collaborative chat (UX-04, D3v2): who sees what you send, and the
 * per-turn "share tool results" choice in agent chats. A solo chat and a flag-off build get neither.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const append = vi.hoisted(() => vi.fn());
const effectiveAgentId = vi.hoisted(() => ({ value: null as string | null }));
const inputMounts = vi.hoisted(() => ({ count: 0 }));

vi.mock('@assistant-ui/react', () => ({ useThreadRuntime: () => ({ append }) }));
vi.mock('@/chat/hooks/use-effective-agent-id', () => ({ useEffectiveAgentId: () => effectiveAgentId.value }));
vi.mock('@/chat/utils/fetch-models-for-context', () => ({ fetchModelsForContext: () => Promise.resolve([]) }));
vi.mock('@/chat/api', () => ({ ChatApi: {} }));
vi.mock('@/lib/api', () => ({
  isRequestCancelledError: () => false,
  isSearchNoAccessibleDocumentsNotFound: () => false,
}));
vi.mock('../../chat-input', () => ({
  ChatInput: function ChatInputMock({ onSend }: { onSend: (m: string) => void }) {
    React.useEffect(() => {
      inputMounts.count += 1;
    }, []);
    return (
      <button type="button" onClick={() => onSend('hello')}>
        send
      </button>
    );
  },
}));

import { AudienceNotice } from '../audience-notice';
import { ChatInputWrapper } from '../chat-input-wrapper';
import { useChatStore } from '@/chat/store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useUserStore } from '@/lib/store/user-store';

const initialChat = useChatStore.getState();

describe('AudienceNotice', () => {
  const renderNotice = (props: Partial<React.ComponentProps<typeof AudienceNotice>> = {}) => {
    const onChange = vi.fn();
    render(
      <Theme>
        <AudienceNotice
          participantCount={3}
          isAgentChat={false}
          shareToolResults={false}
          onShareToolResultsChange={onChange}
          {...props}
        />
      </Theme>,
    );
    return onChange;
  };
  afterEach(cleanup);

  it('says how many people see the chat, that the owner can add more, and that files are shared', () => {
    renderNotice({ participantCount: 3 });

    expect(screen.getByTestId('audience-notice').textContent).toContain(
      'Visible to 3 people · the owner can add more · files you add are shared',
    );
  });

  it('uses the singular for one person', () => {
    renderNotice({ participantCount: 1 });

    expect(screen.getByTestId('audience-notice').textContent).toContain('Visible to 1 person ·');
  });

  it('does not invent a count when the list is hidden from the viewer', () => {
    renderNotice({ participantCount: undefined });

    expect(screen.getByTestId('audience-notice').textContent).toContain('Visible to everyone in this chat');
  });

  it('offers the tool-results checkbox only in agent chats, unticked by default', () => {
    renderNotice({ isAgentChat: false });
    expect(screen.queryByRole('checkbox')).toBeNull();
    cleanup();

    const onChange = renderNotice({ isAgentChat: true });
    const box = screen.getByRole('checkbox', { name: /share tool results/i });
    expect(box.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(box);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('ChatInputWrapper consent', () => {
  function setup(opts: { flag: boolean; collaborative: boolean; agent?: boolean; collaboratorCount?: number }) {
    effectiveAgentId.value = opts.agent ? 'agent-1' : null;
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: opts.flag } });
    useUserStore.setState({ profile: { userId: 'a' } as never });
    const slotId = useChatStore.getState().createSlot('conv-1');
    useChatStore.setState({
      activeSlotId: slotId,
      conversations: opts.collaboratorCount === undefined ? [] : [{ id: 'conv-1', collaboratorCount: opts.collaboratorCount } as never],
    });
    useChatStore.getState().updateSlot(slotId, {
      access: {
        role: 'owner', isOwner: true, accessLevel: 'owner', canSend: true, canManage: true, canInvite: true,
        isCollaborative: opts.collaborative,
      },
    });
  }
  const lastCustom = () => append.mock.calls.at(-1)?.[0].metadata.custom as Record<string, unknown>;

  beforeEach(() => {
    append.mockReset();
    useChatStore.setState({ ...initialChat, slots: {}, activeSlotId: null });
  });
  afterEach(cleanup);

  const mount = () => render(<Theme><ChatInputWrapper /></Theme>);

  it('keeps the composer mounted (draft and focus) when the chat turns shared', () => {
    setup({ flag: true, collaborative: false });
    inputMounts.count = 0;
    mount();
    expect(screen.queryByTestId('audience-notice')).toBeNull();

    act(() => {
      const slotId = useChatStore.getState().activeSlotId!;
      const access = useChatStore.getState().slots[slotId]!.access!;
      useChatStore.getState().updateSlot(slotId, { access: { ...access, isCollaborative: true } });
    });

    expect(screen.getByTestId('audience-notice')).toBeTruthy();
    expect(inputMounts.count).toBe(1);
  });

  it('shows the notice with the owner counted, in a collaborative chat', () => {
    setup({ flag: true, collaborative: true, collaboratorCount: 2 });
    mount();

    expect(screen.getByTestId('audience-notice').textContent).toContain('Visible to 3 people');
  });

  it('shows nothing extra in a solo chat or with the flag off', () => {
    setup({ flag: true, collaborative: false });
    mount();
    expect(screen.queryByTestId('audience-notice')).toBeNull();
    cleanup();

    setup({ flag: false, collaborative: true });
    mount();
    expect(screen.queryByTestId('audience-notice')).toBeNull();
  });

  it('sends shareToolResults for one turn only, then falls back to private', async () => {
    setup({ flag: true, collaborative: true, agent: true });
    mount();

    fireEvent.click(screen.getByRole('checkbox', { name: /share tool results/i }));
    fireEvent.click(screen.getByText('send'));
    expect(lastCustom().shareToolResults).toBe(true);

    expect(screen.getByRole('checkbox', { name: /share tool results/i }).getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByText('send'));
    expect(lastCustom().shareToolResults).toBeUndefined();
  });

  it('has no checkbox outside agent chats', () => {
    setup({ flag: true, collaborative: true, agent: false });
    mount();

    expect(screen.queryByRole('checkbox')).toBeNull();
    fireEvent.click(screen.getByText('send'));
    expect(lastCustom().shareToolResults).toBeUndefined();
  });
});
