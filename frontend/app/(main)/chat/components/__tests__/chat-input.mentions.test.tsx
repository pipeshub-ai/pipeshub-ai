import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
const streaming = vi.hoisted(() => ({
  cancelStreamForSlot: vi.fn(),
  streamRegenerateForSlot: vi.fn(),
}));
const speech = vi.hoisted(() => ({
  state: {
    isListening: false,
    isSupported: true,
    transcript: '',
    interimTranscript: '',
    unavailableReason: null as null | 'stt-not-configured' | 'stt-loading',
  },
  toggle: vi.fn(),
  stop: vi.fn(),
  resetTranscript: vi.fn(),
  onError: null as null | ((error: string) => void),
}));
const device = vi.hoisted(() => ({ isMobile: false }));

vi.mock('next/navigation', () => ({ useRouter: () => router }));

vi.mock('@/chat/streaming', () => ({
  cancelStreamForSlot: (...args: unknown[]) => streaming.cancelStreamForSlot(...args),
  streamRegenerateForSlot: (...args: unknown[]) => streaming.streamRegenerateForSlot(...args),
}));

vi.mock('@/lib/hooks/use-chat-speech-recognition', () => ({
  useChatSpeechRecognition: (opts: { onError?: (error: string) => void }) => {
    speech.onError = opts.onError ?? null;
    return {
      ...speech.state,
      toggle: speech.toggle,
      stop: speech.stop,
      resetTranscript: speech.resetTranscript,
    };
  },
}));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => device.isMobile }));

vi.mock('@/chat/components/chat-panel', async () => {
  const plus = await vi.importActual<typeof import('../chat-panel/plus-menu-button')>(
    '../chat-panel/plus-menu-button',
  );
  const switcher = await vi.importActual<typeof import('../chat-panel/agent-strategy-mode-switcher')>(
    '../chat-panel/agent-strategy-mode-switcher',
  );
  const strategyPanel = await vi.importActual<
    typeof import('../chat-panel/expansion-panels/agent-strategy-mode-panel')
  >('../chat-panel/expansion-panels/agent-strategy-mode-panel');
  return { ...plus, ...switcher, ...strategyPanel };
});

vi.mock('@/chat/components/chat-panel/expansion-panels/chat-input-overlay-panel', () => ({
  ChatInputOverlayPanel: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div role="dialog" aria-label="Expanded panel">{children}</div> : null,
}));

vi.mock('@/chat/components/chat-panel/expansion-panels/model-selector/model-selector-panel', () => ({
  getReasoningEffortLabel: (_t: unknown, effort: string) => `Effort ${effort}`,
  ModelSelectorPanel: ({ onModelSelect }: { onModelSelect: (m: unknown) => void }) => (
    <button
      type="button"
      onClick={() =>
        onModelSelect({ modelKey: 'k-fast', modelName: 'fast-1', modelFriendlyName: 'Fast One' })
      }
    >
      Pick Fast One
    </button>
  ),
}));

vi.mock(
  '@/chat/components/chat-panel/expansion-panels/connectors-collections/connectors-collections-panel',
  () => ({
    ConnectorsCollectionsPanel: ({
      onSelectionChange,
      onToggleView,
    }: {
      onSelectionChange: (next: { apps: string[]; kb: string[] }) => void;
      onToggleView: () => void;
    }) => (
      <div>
        <button type="button" onClick={() => onSelectionChange({ apps: [], kb: ['kb-handbook'] })}>
          Choose the handbook
        </button>
        <button type="button" onClick={onToggleView}>
          Toggle panel size
        </button>
      </div>
    ),
  }),
);

vi.mock('@/chat/components/chat-panel/expansion-panels/agent-scoped-resources-panel', () => ({
  AgentScopedResourcesPanel: ({ scope }: { scope: string }) => <p>Scoped resources for {scope}</p>,
}));

vi.mock('@/chat/components/chat-panel/expansion-panels/universal-agent-resources-panel', () => ({
  UniversalAgentResourcesPanel: ({ onToggleView }: { onToggleView: () => void }) => (
    <div>
      <p>Universal agent resources</p>
      <button type="button" onClick={onToggleView}>
        Toggle panel size
      </button>
    </div>
  ),
}));

vi.mock('@/chat/components/chat-panel/expansion-panels/mobile-query-options-sheet', () => ({
  MobileQueryOptionsSheet: ({ open }: { open: boolean }) => (open ? <p>Query options</p> : null),
}));

import { ChatInput } from '../chat-input';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import type { Editor } from '@tiptap/react';
import { useChatStore } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import type { AttachmentRef } from '@/chat/types';
import { useParticipantsStore } from '@/chat/mentions/participants-store';

const initialChatState = useChatStore.getState();

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const collab = vi.hoisted(() => ({ getCollaborators: vi.fn() }));
vi.mock('@/chat/collaboration-api', () => ({ CollaborationApi: { getCollaborators: collab.getCollaborators } }));

beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  useChatStore.setState({ settings: { ...initialChatState.settings, queryMode: 'chat' } });
  useToastStore.setState({ toasts: [] });
  const slotId = useChatStore.getState().createSlot('conv-1');
  useChatStore.getState().updateSlot(slotId, { isInitialized: true });
  useChatStore.getState().setActiveSlot(slotId);
  router.push.mockReset();
  streaming.cancelStreamForSlot.mockReset();
  streaming.streamRegenerateForSlot.mockReset();
  speech.state = { isListening: false, isSupported: true, transcript: '', interimTranscript: '', unavailableReason: null };
  speech.toggle.mockReset();
  speech.stop.mockReset();
  speech.resetTranscript.mockReset();
  device.isMobile = false;
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  const rect = { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, toJSON: () => ({}) };
  Range.prototype.getBoundingClientRect = () => rect as DOMRect;
  Range.prototype.getClientRects = () =>
    ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
  document.elementFromPoint = () => null;
  useParticipantsStore.getState().reset();
  collab.getCollaborators.mockReset();
  collab.getCollaborators.mockResolvedValue({
    owner: { userId: 'owner-1', displayName: 'Olive Owner' },
    collaborators: [{ principalType: 'user', principalId: 'u-bob', displayName: 'Bob Builder', accessLevel: 'write', state: 'active' }],
    collaboratorCount: 1,
    settings: { editorsCanInvite: false, ownerContentShared: false },
  });
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  useFeatureFlagsStore.setState({ flags: null });
});

type Props = React.ComponentProps<typeof ChatInput>;

function renderInput(props: Partial<Props> = {}) {
  const onSend = vi.fn();
  const utils = render(
    <Theme>
      <ChatInput onSend={onSend} {...props} />
    </Theme>,
  );
  return { onSend, ...utils };
}

const box = () => screen.getByTestId('chat-composer');
const editorOf = () => (box() as unknown as { editor: Editor }).editor;
const sendButton = () => screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement;

async function ready() {
  await waitFor(() => expect(box().getAttribute('contenteditable')).toBeTruthy());
}

async function insert(text: string) {
  await act(async () => {
    editorOf().chain().focus('end').insertContent(text).run();
  });
}

const longText = Array.from({ length: 200 }, (_, i) => `Line ${i} of the pasted log`).join('\n');

describe('ChatInput with ENABLE_CHAT_MENTIONS', () => {
  it('PH10-01: with the flag off (or not loaded) the textarea is rendered', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: false } });
    renderInput();
    expect(box().tagName).toBe('TEXTAREA');
    cleanup();
    useFeatureFlagsStore.setState({ flags: null });
    renderInput();
    expect(box().tagName).toBe('TEXTAREA');
    cleanup();
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: false, ENABLE_CHAT_MENTIONS: true } });
    renderInput();
    expect(box().tagName, 'mentions need collaborative chats too').toBe('TEXTAREA');
  });

  it('renders the rich editor behind the same test id and label when the flag is on', async () => {
    renderInput();
    await ready();
    expect(box().tagName).toBe('DIV');
    expect(box().getAttribute('role')).toBe('textbox');
    expect(box().getAttribute('aria-label')).toBe('Ask anything...');
  });

  it('sends typed text with the two-argument call, Enter included, and clears the editor', async () => {
    const { onSend } = renderInput();
    await ready();
    expect(sendButton().disabled).toBe(true);
    await insert('What changed in Q3?');
    expect(sendButton().disabled).toBe(false);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('What changed in Q3?', undefined);
    await waitFor(() => expect(box().textContent).toBe(''));
  });

  it('sends the wire text and the mention refs as a third argument', async () => {
    const { onSend } = renderInput();
    await ready();
    await insert('@');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Bob Builder' })).toBeTruthy());
    fireEvent.click(screen.getByRole('option', { name: 'Bob Builder' }));
    await insert('what do you think?');
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('<@user:u-bob> what do you think?', undefined, [{ type: 'user', id: 'u-bob' }]);
  });

  it('PH10-06: Shift+Enter and an IME Enter do not send', async () => {
    const { onSend } = renderInput();
    await ready();
    await insert('Line one');
    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(box(), { key: 'Enter', isComposing: true, keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('Enter picks a mention option instead of sending while the popover is open', async () => {
    const { onSend } = renderInput();
    await ready();
    await insert('hi @bob');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Bob Builder' })).toBeTruthy());
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('mention-chip')).toBeTruthy());
  });

  it('PH10-03: a finished dictation is appended with a space, and words still being recognised are not committed', async () => {
    speech.state = { ...speech.state, isListening: true, interimTranscript: 'what is' };
    const { rerender, onSend } = renderInput();
    await ready();
    await insert('Draft:');
    expect(box().querySelector('.ph-composer-interim')?.textContent).toBe(' what is');
    speech.state = { ...speech.state, transcript: 'send the report today', interimTranscript: '' };
    rerender(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await waitFor(() => expect(box().textContent).toBe('Draft: send the report today'));
    expect(speech.resetTranscript).toHaveBeenCalled();
    expect(box().querySelector('.ph-composer-interim')).toBeNull();
  });

  it('PH10-04: a pasted file still becomes an attachment chip', async () => {
    const uploaded: File[] = [];
    renderInput({ onUploadFile: (f) => { uploaded.push(f); return new Promise<AttachmentRef>(() => {}); } });
    await ready();
    const file = new File(['%PDF'], 'report.pdf', { type: 'application/pdf' });
    fireEvent.paste(box(), { clipboardData: { items: [{ kind: 'file', type: file.type, getAsFile: () => file }], getData: () => '' } });
    expect(uploaded).toHaveLength(1);
    expect(screen.getByText('report.pdf')).toBeTruthy();
    expect(box().textContent).toBe('');
  });

  it('PH10-04: a very long paste becomes a pasted-text chip and short text is inserted, as with the textarea', async () => {
    renderInput({ onUploadFile: () => new Promise<AttachmentRef>(() => {}) });
    await ready();
    act(() => {
      editorOf().commands.focus();
    });
    fireEvent.paste(box(), { clipboardData: { items: [], getData: () => longText } });
    expect(screen.getByText('Pasted text')).toBeTruthy();
    expect(box().textContent).toBe('');

    fireEvent.paste(box(), { clipboardData: { items: [], getData: () => 'short note' } });
    await waitFor(() => expect(box().textContent).toBe('short note'));
  });

  it('PH10-04: Shift held pastes even a long text as plain text', async () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });
    await ready();
    act(() => {
      editorOf().commands.focus();
    });
    fireEvent.keyDown(window, { key: 'Shift', shiftKey: true });
    fireEvent.paste(box(), { clipboardData: { items: [], getData: () => longText } });
    expect(onUploadFile).not.toHaveBeenCalled();
    await waitFor(() => expect(box().textContent).toContain('Line 199 of the pasted log'));
  });

  it('PH10-02: regenerate shows the question read-only; Escape empties it', async () => {
    renderInput();
    await ready();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-2', text: 'Earlier question' });
    });
    await waitFor(() => expect(box().textContent).toBe('Earlier question'));
    expect(box().getAttribute('contenteditable')).toBe('false');
    fireEvent.keyDown(box(), { key: 'Escape' });
    await waitFor(() => expect(box().textContent).toBe(''));
    expect(screen.queryByText('Regenerate response')).toBeNull();
    expect(box().getAttribute('contenteditable')).toBe('true');
  });

  it('PH10-02: edit query is editable and focused, with the stored mention shown as a chip', async () => {
    renderInput();
    await ready();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'ask <@user:u-bob> again' });
    });
    await waitFor(() => expect(screen.getByTestId('mention-chip').textContent).toBe('@Bob Builder'));
    expect(box().getAttribute('contenteditable')).toBe('true');
    await waitFor(() => expect(document.activeElement).toBe(box()));
    fireEvent.keyDown(box(), { key: 'Escape' });
    await waitFor(() => expect(box().textContent).toBe(''));
  });
});

describe('mentioning an agent that is not the chat’s own', () => {
  it('is refused with a notice before anything is sent, and the chip stays', async () => {
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await ready();
    await act(async () => {
      editorOf()
        .chain()
        .focus('end')
        .insertContent({ type: 'mention', attrs: { id: 'agent-9', label: 'Offer drafter', mentionType: 'agent' } })
        .run();
    });
    await insert(' check this');
    fireEvent.click(sendButton());

    expect(onSend).not.toHaveBeenCalled();
    expect(useToastStore.getState().toasts.map((x) => x.title)).toEqual(["This agent can't answer in this chat yet. Open its own chat to use it."]);
    expect(screen.getByTestId('mention-chip').textContent).toContain('Offer drafter');
    const [notice] = useToastStore.getState().toasts;
    expect(notice.action?.label).toBe('Open agent chat');
    router.replace.mockReset();
    notice.action?.onClick?.();
    expect(router.replace).toHaveBeenCalledWith('/chat/?agentId=agent-9');
  });
});

describe('typed mentions on submit', () => {
  const BOB = { type: 'user', id: 'u-bob' };
  const BOBBY = { type: 'user', id: 'u-bobby' };

  function withBobAndBobby() {
    collab.getCollaborators.mockResolvedValue({
      owner: { userId: 'owner-1', displayName: 'Olive Owner' },
      collaborators: [
        { principalType: 'user', principalId: 'u-bob', displayName: 'Bob', accessLevel: 'write', state: 'active' },
        { principalType: 'user', principalId: 'u-bobby', displayName: 'Bobby', accessLevel: 'read', state: 'active' },
      ],
      collaboratorCount: 2,
      settings: { editorsCanInvite: false, ownerContentShared: false },
    });
  }

  it('MN-02: a typed exact name is sent as a token with its mention', async () => {
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await ready();
    await waitFor(() => expect(useParticipantsStore.getState().byConv['conv-1']).toBeTruthy());
    await insert('please look @Bob Builder');
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('please look <@user:u-bob>', undefined, [BOB]);
  });

  it('MN-01: a typed alias reaches the assistant without a token', async () => {
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await ready();
    await insert('@assistant summarize');
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('@assistant summarize', undefined, [{ type: 'assistant', id: 'self' }]);
  });

  it('MN-03: an ambiguous @Bo opens the chooser, sends nothing until a pick, then sends the pick', async () => {
    withBobAndBobby();
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await ready();
    await waitFor(() => expect(useParticipantsStore.getState().byConv['conv-1']).toBeTruthy());
    await insert('hello @Bo');
    fireEvent.click(sendButton());
    await waitFor(() => expect(screen.getByTestId('mention-chooser')).toBeTruthy());
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getAllByTestId('mention-chooser-option').map((o) => o.textContent)).toEqual(['Bob· person', 'Bobby· person']);

    fireEvent.click(screen.getAllByTestId('mention-chooser-option')[1]);
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('hello <@user:u-bobby>', undefined, [BOBBY]);
    expect(screen.queryByTestId('mention-chooser')).toBeNull();
  });

  it('MN-03: dismissing the chooser keeps the draft and sends nothing', async () => {
    withBobAndBobby();
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    await ready();
    await waitFor(() => expect(useParticipantsStore.getState().byConv['conv-1']).toBeTruthy());
    await insert('hello @Bo');
    fireEvent.click(sendButton());
    await waitFor(() => expect(screen.getByTestId('mention-chooser')).toBeTruthy());
    fireEvent.keyDown(screen.getByTestId('mention-chooser'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('mention-chooser')).toBeNull());
    expect(onSend).not.toHaveBeenCalled();
    expect(box().textContent).toContain('hello @Bo');
  });

  it('with the flag off nothing is resolved: the text goes out as typed', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: false } });
    const onSend = vi.fn();
    render(
      <Theme>
        <ChatInput onSend={onSend} />
      </Theme>,
    );
    fireEvent.change(box(), { target: { value: '@assistant and @Bob Builder' } });
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('@assistant and @Bob Builder', undefined);
    expect(collab.getCollaborators).not.toHaveBeenCalled();
  });
});
