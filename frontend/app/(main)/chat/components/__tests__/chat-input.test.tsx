import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act, within } from '@testing-library/react';
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
import { useChatStore } from '@/chat/store';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import type { AttachmentRef } from '@/chat/types';

const initialChatState = useChatStore.getState();

class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  useChatStore.setState(initialChatState, true);
  useChatStore.setState({ settings: { ...initialChatState.settings, queryMode: 'chat' } });
  useToastStore.setState({ toasts: [] });
  router.push.mockReset();
  streaming.cancelStreamForSlot.mockReset();
  streaming.streamRegenerateForSlot.mockReset();
  speech.state = {
    isListening: false,
    isSupported: true,
    transcript: '',
    interimTranscript: '',
    unavailableReason: null,
  };
  speech.toggle.mockReset();
  speech.stop.mockReset();
  speech.resetTranscript.mockReset();
  device.isMobile = false;
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText('Ask anything...') as HTMLTextAreaElement;
}

function sendButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement;
}

function type(text: string) {
  fireEvent.change(composer(), { target: { value: text } });
}

function toastTitles(): string[] {
  return useToastStore.getState().toasts.map((t) => t.title);
}

function fileInput(): HTMLInputElement {
  const el = document.querySelector('input[type="file"]');
  if (!(el instanceof HTMLInputElement)) throw new Error('hidden file input not rendered');
  return el;
}

function pick(...files: File[]) {
  fireEvent.change(fileInput(), { target: { files } });
}

function pdf(name = 'report.pdf', size = 2048): File {
  const file = new File(['%PDF'], name, { type: 'application/pdf' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function startStreamingSlot(patch: Record<string, unknown> = {}) {
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, isStreaming: true, ...patch });
  store.setActiveSlot(slotId);
  return slotId;
}

function ref(recordId: string): AttachmentRef {
  return {
    recordId,
    recordName: `${recordId}.pdf`,
    mimeType: 'application/pdf',
    extension: 'pdf',
    virtualRecordId: `v-${recordId}`,
  };
}

describe('ChatInput — typing and sending', () => {
  it('sends what the user typed and clears the box', () => {
    const { onSend } = renderInput();

    expect(sendButton().disabled).toBe(true);
    type('What changed in the Q3 plan?');
    expect(sendButton().disabled).toBe(false);
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('What changed in the Q3 plan?', undefined);
    expect(composer().value).toBe('');
  });

  it('names the send button for screen readers instead of reading out its icon', () => {
    renderInput();

    expect(screen.getByRole('button', { name: 'Send message' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'arrow_upward' })).toBeNull();
  });

  it('sends on Enter but not on Shift+Enter or while an IME is composing', () => {
    const { onSend } = renderInput();
    type('Line one');

    fireEvent.keyDown(composer(), { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(composer(), { key: 'Enter', isComposing: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Line one', undefined);
  });

  it('does not send a message made only of spaces', () => {
    const { onSend } = renderInput();
    type('   ');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(sendButton().disabled).toBe(true);
  });

  it('offers Stop instead of Send while an answer is streaming, and stops it', () => {
    const slotId = startStreamingSlot();
    const { onSend } = renderInput();
    type('A follow-up');

    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Stop generating' }));

    expect(streaming.cancelStreamForSlot).toHaveBeenCalledWith(slotId);
    expect(toastTitles()).toContain('Stopping…');
  });

  it('disables Stop once stopping has begun so a second click cannot cancel twice', () => {
    startStreamingSlot({ stopping: true });
    renderInput();

    const stop = screen.getByRole('button', { name: 'Stop generating' }) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    fireEvent.click(stop);
    expect(streaming.cancelStreamForSlot).not.toHaveBeenCalled();
  });

  it('lets a follow-up through once the stopped run is winding down', () => {
    startStreamingSlot({ stopping: true });
    const { onSend } = renderInput();
    type('Next question');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('Next question', undefined);
  });

  it('waits for the agent tools to finish loading before it can send', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, queryMode: 'agent' },
      universalAgentToolsLoading: true,
    });
    const { onSend } = renderInput();
    type('Book a meeting');

    expect(sendButton().disabled).toBe(true);
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('seeds the box from a suggestion, and re-seeds only when a new suggestion is picked', () => {
    const { rerender } = renderInput({ prefill: { text: 'Summarise my week', key: 1 } });
    expect(composer().value).toBe('Summarise my week');

    type('Summarise my week, please');
    rerender(
      <Theme>
        <ChatInput onSend={vi.fn()} prefill={{ text: 'Summarise my week', key: 1 }} />
      </Theme>,
    );
    expect(composer().value).toBe('Summarise my week, please');

    rerender(
      <Theme>
        <ChatInput onSend={vi.fn()} prefill={{ text: 'Summarise my week', key: 2 }} />
      </Theme>,
    );
    expect(composer().value).toBe('Summarise my week');
  });
});

describe('ChatInput — attachments', () => {
  it('uploads a picked file straight away and sends its reference with the message', async () => {
    const upload = deferred<AttachmentRef>();
    const onUploadFile = vi.fn(() => upload.promise);
    const { onSend } = renderInput({ onUploadFile });

    pick(pdf());
    expect(onUploadFile).toHaveBeenCalledTimes(1);
    expect(screen.getAllByLabelText('Uploading report.pdf').length).toBeGreaterThan(0);

    type('Summarise the attached report');
    expect(sendButton().disabled).toBe(true);
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();

    await act(async () => upload.resolve(ref('rec-1')));
    expect(screen.queryByLabelText('Uploading report.pdf')).toBeNull();
    expect(screen.getByText('2 KB')).toBeTruthy();

    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('Summarise the attached report', [ref('rec-1')]);
    expect(screen.queryByText('report.pdf')).toBeNull();
  });

  it('can send an attachment without any typed text', async () => {
    const { onSend } = renderInput({ onUploadFile: vi.fn(async () => ref('rec-1')) });

    await act(async () => pick(pdf()));
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('', [ref('rec-1')]);
  });

  it('refuses an unsupported file type and says which types are allowed', () => {
    const onUploadFile = vi.fn();
    renderInput({ onUploadFile });

    pick(new File(['MZ'], 'setup.exe', { type: 'application/x-msdownload' }));

    expect(onUploadFile).not.toHaveBeenCalled();
    expect(toastTitles()).toEqual([
      'Unsupported file type: setup.exe. Supported types: PDF, PNG, JPEG, JPG, TXT, MD, DOCX, XLSX, CSV.',
    ]);
  });

  it('refuses a file over the size limit and says what the limit is', () => {
    const onUploadFile = vi.fn();
    renderInput({ onUploadFile });

    pick(pdf('huge.pdf', 6 * 1024 * 1024));

    expect(onUploadFile).not.toHaveBeenCalled();
    expect(toastTitles()).toEqual(['File too large: huge.pdf. Maximum size is 5 MB per file.']);
  });

  it('keeps at most ten attachments and says so', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });

    pick(...Array.from({ length: 12 }, (_, i) => pdf(`doc-${i}.pdf`)));

    expect(onUploadFile).toHaveBeenCalledTimes(10);
    expect(toastTitles()).toEqual(['Maximum 10 attachments per message.']);
    expect(screen.queryByText('doc-10.pdf')).toBeNull();
  });

  it('shows why an upload failed, and a retry that uploads again', async () => {
    const onUploadFile = vi
      .fn<(file: File, signal: AbortSignal) => Promise<AttachmentRef>>()
      .mockRejectedValueOnce(new Error('The file server is busy. Try again in a minute.'))
      .mockResolvedValueOnce(ref('rec-2'));
    renderInput({ onUploadFile });

    await act(async () => pick(pdf()));

    const reason = "Couldn't attach report.pdf. The file server is busy. Try again in a minute.";
    expect(screen.getByText(reason)).toBeTruthy();
    expect(toastTitles()).toEqual([`Failed to upload report.pdf: ${reason}`]);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Retry uploading report.pdf' }));
    });

    expect(onUploadFile).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(reason)).toBeNull();
    expect(screen.getByText('2 KB')).toBeTruthy();
  });

  it('sends the text with only the attachments that uploaded when one failed', async () => {
    const onUploadFile = vi
      .fn<(file: File, signal: AbortSignal) => Promise<AttachmentRef>>()
      .mockResolvedValueOnce(ref('rec-ok'))
      .mockRejectedValueOnce(new Error('boom'));
    const { onSend } = renderInput({ onUploadFile });

    await act(async () => pick(pdf('good.pdf'), pdf('bad.pdf')));
    type('Compare these');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('Compare these', [ref('rec-ok')]);
  });

  it('keeps a failed attachment and its retry when there is nothing else to send', async () => {
    const onUploadFile = vi.fn(async (): Promise<AttachmentRef> => {
      throw new Error('The file server is busy. Try again in a minute.');
    });
    const { onSend } = renderInput({ onUploadFile });

    await act(async () => pick(pdf()));
    expect(sendButton().disabled).toBe(true);
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText('report.pdf')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry uploading report.pdf' })).toBeTruthy();
  });

  it('deletes an uploaded file on the server when its chip is removed', async () => {
    const onDeleteFile = vi.fn();
    renderInput({ onUploadFile: vi.fn(async () => ref('rec-9')), onDeleteFile });

    await act(async () => pick(pdf()));
    fireEvent.click(screen.getByRole('button', { name: 'Remove report.pdf' }));

    expect(onDeleteFile).toHaveBeenCalledWith('rec-9');
    expect(screen.queryByText('report.pdf')).toBeNull();
  });

  it('abandons uploads still in flight when the composer goes away, without an error message', async () => {
    const upload = deferred<AttachmentRef>();
    let signal: AbortSignal | undefined;
    const { unmount } = renderInput({
      onUploadFile: (_file, s) => {
        signal = s;
        return upload.promise;
      },
    });
    pick(pdf());

    unmount();

    expect(signal?.aborted).toBe(true);
    await act(async () => upload.reject(new Error('aborted')));
    expect(toastTitles()).toEqual([]);
  });

  it('attaches a pasted screenshot under a generated name', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });
    const shot = new File(['png'], 'image', { type: 'image/png' });

    fireEvent.paste(composer(), {
      clipboardData: {
        items: [{ kind: 'file', getAsFile: () => shot }],
        getData: () => '',
      },
    });

    expect(onUploadFile).toHaveBeenCalledTimes(1);
    const uploaded = onUploadFile.mock.calls[0] as unknown as [File];
    expect(uploaded[0].name).toMatch(/^pasted-\d+\.png$/);
  });

  it('turns a very long paste into a text attachment, unless Shift is held', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });
    const longText = Array.from({ length: 200 }, (_, i) => `Line ${i} of the pasted log`).join('\n');
    const clipboardData = { items: [], getData: () => longText };

    fireEvent.keyDown(window, { key: 'Shift', shiftKey: true });
    fireEvent.paste(composer(), { clipboardData });
    expect(onUploadFile).not.toHaveBeenCalled();

    fireEvent.keyUp(window, { key: 'Shift', shiftKey: false });
    fireEvent.paste(composer(), { clipboardData });
    expect(onUploadFile).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Pasted text')).toBeTruthy();
  });

  it('ignores pasted files in web search, which takes no attachments', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, queryMode: 'web-search' } });
    const onUploadFile = vi.fn();
    renderInput({ onUploadFile });

    fireEvent.paste(composer(), {
      clipboardData: {
        items: [{ kind: 'file', getAsFile: () => pdf() }],
        getData: () => '',
      },
    });

    expect(onUploadFile).not.toHaveBeenCalled();
  });

  it('accepts files dropped anywhere on the composer', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });
    const dataTransfer = { types: ['Files'], files: [pdf('dropped.pdf')] };

    fireEvent.dragEnter(composer(), { dataTransfer });
    expect(screen.getByText('Drop files here')).toBeTruthy();
    fireEvent.drop(composer(), { dataTransfer });

    expect(onUploadFile).toHaveBeenCalledTimes(1);
    expect(screen.getByText('dropped.pdf')).toBeTruthy();
    expect(screen.queryByText('Drop files here')).toBeNull();
  });

  it('opens the upload area from the + menu and shows the attachment limits', () => {
    renderInput({ onUploadFile: vi.fn() });

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Attach files and capabilities' }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Attach files and capabilities' }));
    fireEvent.click(screen.getByText('Attach files'));

    expect(screen.getByText('Upload your File')).toBeTruthy();
    expect(screen.getByText('Up to 10 files per message')).toBeTruthy();
    expect(
      screen.getByText('Supports: PDF, PNG, JPEG, JPG, TXT, MD, DOCX, XLSX, CSV.'),
    ).toBeTruthy();
  });
});

describe('ChatInput — regenerate and edit a previous message', () => {
  it('regenerates the chosen answer with the filters it was first asked with', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    const { onSend } = renderInput();

    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', {
        messageId: 'msg-7',
        text: 'Which vendors renewed?',
        appliedFilters: {
          apps: [{ id: 'app-drive', name: 'Drive', nodeType: 'app', connector: 'DRIVE' }],
          kb: [{ id: 'kb-legal', name: 'Legal', nodeType: 'recordGroup', connector: 'KB' }],
        },
      });
    });

    expect(screen.getByText('Regenerate response')).toBeTruthy();
    expect(composer().value).toBe('Which vendors renewed?');
    expect(composer().readOnly).toBe(true);
    expect(screen.getByText('Drive')).toBeTruthy();
    expect(screen.getByText('Legal')).toBeTruthy();

    fireEvent.click(sendButton());

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(slotId, 'msg-7', undefined, {
      apps: ['app-drive'],
      kb: ['kb-legal'],
    });
    expect(onSend).not.toHaveBeenCalled();
    expect(composer().value).toBe('');
    expect(screen.queryByText('Regenerate response')).toBeNull();
  });

  it('leaves regenerate mode on Escape without regenerating', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Old question' });
    });

    fireEvent.keyDown(composer(), { key: 'Escape' });

    expect(screen.queryByText('Regenerate response')).toBeNull();
    expect(composer().value).toBe('');
    expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();
  });

  it('leaves regenerate mode when the user opens another conversation', () => {
    const store = useChatStore.getState();
    const other = store.createSlot('conv-2');
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Old question' });
    });

    act(() => useChatStore.getState().setActiveSlot(other));

    expect(screen.queryByText('Regenerate response')).toBeNull();
  });

  it('ignores a regenerate request that does not say which message', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { text: 'No id' });
      useCommandStore.getState().dispatch('showRegenBar', 'not an object');
      useCommandStore.getState().dispatch('showEditQuery', { text: 'No id either' });
    });

    expect(screen.queryByText('Regenerate response')).toBeNull();
    expect(screen.queryByText('Edit Query')).toBeNull();
  });

  it('lets the user edit an earlier question, and says editing is not available yet on send', () => {
    const { onSend } = renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    expect(screen.getByText('Edit Query')).toBeTruthy();
    expect(composer().value).toBe('Original question');
    expect(composer().readOnly).toBe(false);

    type('Edited question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(toastTitles()).toEqual(['Coming Soon']);
    expect(composer().value).toBe('');
  });
});

describe('ChatInput — agent checks before sending', () => {
  it('blocks sending to an agent whose tools were removed, and links to the Agent Builder', () => {
    useChatStore.setState({ agentDeprecatedToolNames: ['old_search'] });
    const { onSend } = renderInput({ isAgentChat: true, agentId: 'agent/1' });
    type('Hello agent');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    const [toast] = useToastStore.getState().toasts;
    expect(toast.title).toBe(
      'This agent has tools that are no longer available. Open the Agent Builder to remove them.',
    );
    toast.action?.onClick();
    expect(router.push).toHaveBeenCalledWith('/agents/edit?agentKey=agent%2F1');
  });

  it('refuses a selection of more than 1024 tools', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, queryMode: 'agent' },
      universalAgentStreamTools: Array.from({ length: 1025 }, (_, i) => `inst:tool_${i}`),
    });
    const { onSend } = renderInput();
    type('Do everything');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(toastTitles()).toEqual([
      'Too many tools selected. Maximum 1024 tools are allowed per request due to performance limits.',
    ]);
  });

  it('refuses two selected instances of the same action type and names the type', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, queryMode: 'agent' },
      universalAgentStreamTools: ['jira-a:jira.search', 'jira-b:jira.search'],
      universalAgentToolGroups: [
        { label: 'Jira (team A)', fullNames: ['jira.search'], toolsetSlug: 'jira', instanceId: 'jira-a', isAuthenticated: true },
        { label: 'Jira (team B)', fullNames: ['jira.search'], toolsetSlug: 'jira', instanceId: 'jira-b', isAuthenticated: true },
      ],
    });
    const { onSend } = renderInput();
    type('Find my tickets');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(toastTitles()).toEqual([
      'Multiple instances of the same action type (jira) cannot be used together. Open the Actions panel and select only one instance per type.',
    ]);
  });

  it('sends when only one instance of each action type is selected', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, queryMode: 'agent' },
      universalAgentStreamTools: ['jira-a:jira.search'],
      universalAgentToolGroups: [
        { label: 'Jira (team A)', fullNames: ['jira.search'], toolsetSlug: 'jira', instanceId: 'jira-a', isAuthenticated: true },
        { label: 'Jira (team B)', fullNames: ['jira.search'], toolsetSlug: 'jira', instanceId: 'jira-b', isAuthenticated: true },
      ],
    });
    const { onSend } = renderInput();
    type('Find my tickets');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('Find my tickets', undefined);
  });

  it('with every tool selected, refuses when two instances of one type are connected', () => {
    useChatStore.setState({
      agentChatToolGroups: [
        { label: 'Slack one', fullNames: ['slack.post'], toolsetSlug: 'slack' },
        { label: 'Slack two', fullNames: ['slack.post'], toolsetSlug: 'slack' },
      ],
    });
    const { onSend } = renderInput({ isAgentChat: true, agentId: 'agent-1' });
    type('Post an update');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(toastTitles()[0]).toContain('(slack)');
  });
});

describe('ChatInput — connectors and collections', () => {
  it('shows the selected collections as pills and removes one when its close button is clicked', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, filters: { apps: ['app-drive'], kb: ['kb-handbook'] } },
      collectionNamesCache: { 'kb-handbook': 'Employee Handbook' },
      collectionMetaCache: { 'app-drive': { name: 'Google Drive', nodeType: 'app', connector: 'DRIVE' } },
    });
    renderInput();

    expect(screen.getByText('Google Drive')).toBeTruthy();
    const pill = screen.getByText('Employee Handbook').parentElement as HTMLElement;
    fireEvent.click(within(pill).getByRole('button'));

    expect(useChatStore.getState().settings.filters).toEqual({ apps: ['app-drive'], kb: [] });
    expect(screen.queryByText('Employee Handbook')).toBeNull();
  });

  it('removes a connector pill from the connector filters', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, filters: { apps: ['app-drive'], kb: [] } },
      collectionMetaCache: { 'app-drive': { name: 'Google Drive', nodeType: 'app', connector: 'DRIVE' } },
    });
    renderInput();

    const pill = screen.getByText('Google Drive').parentElement as HTMLElement;
    fireEvent.click(within(pill).getByRole('button'));

    expect(useChatStore.getState().settings.filters.apps).toEqual([]);
  });

  it('shows no collection pills in web search', () => {
    useChatStore.setState({
      settings: {
        ...useChatStore.getState().settings,
        queryMode: 'web-search',
        filters: { apps: [], kb: ['kb-handbook'] },
      },
      collectionNamesCache: { 'kb-handbook': 'Employee Handbook' },
    });
    renderInput();

    expect(screen.queryByText('Employee Handbook')).toBeNull();
  });

  it('shows the agent\'s own collections and restores the defaults when a narrowed one is removed', () => {
    useChatStore.setState({
      agentKnowledgeDefaults: { apps: [], kb: ['kb-a'] },
      agentKnowledgeScope: { apps: [], kb: ['kb-a', 'kb-b'] },
      collectionNamesCache: { 'kb-a': 'Alpha docs', 'kb-b': 'Beta docs' },
    });
    renderInput({ isAgentChat: true, agentId: 'agent-1' });

    const pill = screen.getByText('Beta docs').parentElement as HTMLElement;
    fireEvent.click(within(pill).getByRole('button'));

    expect(useChatStore.getState().agentKnowledgeScope).toBeNull();
  });

  it('opens the connectors panel and applies the collections picked there', () => {
    renderInput();

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    fireEvent.click(screen.getByText('Choose the handbook'));

    expect(useChatStore.getState().settings.filters.kb).toEqual(['kb-handbook']);
  });

  it('can switch the connectors panel to the larger overlay and back', () => {
    renderInput();

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    fireEvent.click(screen.getByText('Toggle panel size'));
    expect(screen.getByRole('dialog', { name: 'Expanded panel' })).toBeTruthy();

    fireEvent.click(within(screen.getByRole('dialog')).getByText('Toggle panel size'));
    expect(screen.queryByRole('dialog', { name: 'Expanded panel' })).toBeNull();
  });

  it('opens the agent resources panel for universal agent mode', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, queryMode: 'agent' } });
    renderInput();

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);

    expect(screen.getByText('Universal agent resources')).toBeTruthy();
  });

  it('opens the scoped resources panel in an agent chat', () => {
    renderInput({ isAgentChat: true, agentId: 'agent-1' });

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);

    expect(screen.getByText('Scoped resources for agent')).toBeTruthy();
  });

  it('closes an open panel when the user clicks outside the composer', () => {
    renderInput();
    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    expect(screen.getByText('Choose the handbook')).toBeTruthy();

    fireEvent.mouseDown(document.body);

    expect(screen.queryByText('Choose the handbook')).toBeNull();
  });
});

describe('ChatInput — model, voice and search view', () => {
  it('shows the model in use and switches it from the model panel', () => {
    useChatStore.setState({
      settings: {
        ...useChatStore.getState().settings,
        defaultModels: { __assistant__: { modelKey: 'k-smart', modelName: 'smart-1', modelFriendlyName: 'Smart One' } },
      },
    });
    renderInput();
    const selector = screen.getAllByTestId('chat-model-selector')[0];
    expect(selector.textContent).toContain('Smart One');

    fireEvent.click(selector);
    fireEvent.click(screen.getByText('Pick Fast One'));

    expect(useChatStore.getState().settings.selectedModels.__assistant__).toEqual(
      expect.objectContaining({ modelName: 'fast-1' }),
    );
  });

  it('adds what the user dictated to the text already typed', () => {
    const { rerender } = renderInput();
    type('Draft:');

    speech.state = { ...speech.state, transcript: 'send the report today' };
    rerender(
      <Theme>
        <ChatInput onSend={vi.fn()} />
      </Theme>,
    );

    expect(composer().value).toBe('Draft: send the report today');
    expect(speech.resetTranscript).toHaveBeenCalled();
  });

  it('shows words still being recognised, and stops listening on send', () => {
    speech.state = { ...speech.state, isListening: true, interimTranscript: 'what is' };
    const { onSend } = renderInput();

    expect((screen.getByPlaceholderText('Listening...') as HTMLTextAreaElement).value).toBe('what is');
    fireEvent.click(sendButton());

    expect(speech.stop).toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it('tells the user when the microphone is blocked', () => {
    renderInput();

    act(() => speech.onError?.('not-allowed'));

    expect(toastTitles()).toEqual(['Could not access microphone']);
  });

  it('explains why voice input is off when no speech model is set up', () => {
    speech.state = { ...speech.state, isSupported: false, unavailableReason: 'stt-not-configured' };
    renderInput();

    const mic = screen.getByRole('button', {
      name: 'Configure a Speech-to-Text (STT) model in AI Models settings to enable voice input.',
    }) as HTMLButtonElement;
    expect(mic.disabled).toBe(true);
  });

  it('switches to the search view and back to chat', () => {
    const newChat = vi.fn();
    useCommandStore.getState().register('newChat', newChat);
    renderInput();

    fireEvent.click(screen.getByRole('button', { name: 'Switch to search view' }));
    expect(newChat).toHaveBeenCalled();
    expect(useChatStore.getState().settings.mode).toBe('search');

    fireEvent.click(screen.getByRole('button', { name: 'Back to chat' }));
    expect(useChatStore.getState().settings.mode).toBe('chat');
    useCommandStore.getState().unregister('newChat');
  });
});

describe('ChatInput — compact widget', () => {
  it('starts as a one-line box that sends on Enter', () => {
    const { onSend } = renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });
    const line = screen.getByPlaceholderText('Ask about this page') as HTMLInputElement;

    fireEvent.change(line, { target: { value: 'What is this doc about?' } });
    fireEvent.keyDown(line, { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('What is this doc about?', undefined);
  });

  it('expands into the full composer when the user asks for more', () => {
    renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });

    fireEvent.click(screen.getByRole('button', { name: 'Expand composer' }));

    expect(document.activeElement).toBe(composer());
  });

  it('offers Stop in the widget while an answer streams', () => {
    const slotId = startStreamingSlot();
    renderInput({ variant: 'widget' });

    fireEvent.click(screen.getByRole('button', { name: 'Stop generating' }));

    expect(streaming.cancelStreamForSlot).toHaveBeenCalledWith(slotId);
  });

  it('has a named send button in the widget too', () => {
    const { onSend } = renderInput({ variant: 'widget' });
    fireEvent.change(screen.getByPlaceholderText('Ask anything...'), { target: { value: 'Hi' } });

    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('Hi', undefined);
  });
});

describe('ChatInput — on a phone', () => {
  it('opens the query options sheet from the more button', () => {
    device.isMobile = true;
    renderInput();

    fireEvent.click(screen.getByText('more_horiz').closest('button') as HTMLButtonElement);

    expect(screen.getByText('Query options')).toBeTruthy();
  });
});
