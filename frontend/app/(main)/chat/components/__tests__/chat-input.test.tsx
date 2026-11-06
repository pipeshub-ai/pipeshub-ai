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
  onError: null as null | ((error: string, refusal?: string) => void),
}));
const device = vi.hoisted(() => ({ isMobile: false }));
const mentionField = vi.hoisted(() => ({
  latest: null as null | { onChange: (value: string) => void; onMentionsChange: (nodes: { id: string; name: string; nodeType: string; connector: string }[]) => void },
  rendered: vi.fn<(mentionsOff: boolean | undefined, context?: { preferredSources?: readonly { id: string; name: string }[] | null }) => void>(),
}));

vi.mock('@/chat/components/mention-composer-field', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/chat/components/mention-composer-field')>();
  type FieldProps = Omit<React.ComponentProps<typeof actual.MentionComposerField>, 'ref'>;
  const Spy = React.forwardRef<HTMLTextAreaElement, FieldProps>(function MentionComposerFieldSpy(props, ref) {
    mentionField.rendered(props.mentionsOff, props.context);
    mentionField.latest = props;
    return <actual.MentionComposerField {...props} ref={ref} />;
  });
  return { ...actual, MentionComposerField: Spy };
});

vi.mock('next/navigation', () => ({ useRouter: () => router }));

const org = vi.hoisted(() => ({
  value: { org: null as null | { shortName?: string; registeredName?: string }, logoUrl: null },
  calls: 0,
}));
vi.mock('@/chat/api', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  fetchOrgWithLogo: () => {
    org.calls += 1;
    return Promise.resolve(org.value);
  },
}));

vi.mock('@/chat/streaming', () => ({
  cancelStreamForSlot: (...args: unknown[]) => streaming.cancelStreamForSlot(...args),
  streamRegenerateForSlot: (...args: unknown[]) => streaming.streamRegenerateForSlot(...args),
}));

vi.mock('@/lib/hooks/use-chat-speech-recognition', () => ({
  useChatSpeechRecognition: (opts: { onError?: (error: string, refusal?: string) => void }) => {
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
  getAppliedReasoningEffortLabel: (_t: unknown, { picked }: { picked: string | null }) => `Effort ${picked}`,
  getReasoningEffortLabel: (_t: unknown, level: string) => level.charAt(0).toUpperCase() + level.slice(1),
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
  UniversalAgentResourcesPanel: ({
    onToggleView,
    onSelectionChange,
  }: {
    onToggleView: () => void;
    onSelectionChange?: (next: { apps: string[]; kb: string[] }) => void;
  }) => (
    <div>
      <p>Universal agent resources</p>
      <button type="button" onClick={() => onSelectionChange?.({ apps: [], kb: ['kb-handbook'] })}>
        Choose the handbook
      </button>
      <button type="button" onClick={onToggleView}>
        Toggle panel size
      </button>
    </div>
  ),
}));

vi.mock('@/chat/components/chat-panel/expansion-panels/mobile-query-options-sheet', () => ({
  MobileQueryOptionsSheet: ({
    open,
    onOpenChange,
    onSelectionChange,
  }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSelectionChange?: (next: { apps: string[]; kb: string[] }) => void;
  }) =>
    open ? (
      <div role="dialog" aria-label="Query options">
        <p>Query options</p>
        <button type="button" onClick={() => onSelectionChange?.({ apps: [], kb: ['kb-handbook'] })}>
          Choose the handbook on the phone
        </button>
        <button type="button" onClick={() => onOpenChange(false)}>
          Done
        </button>
      </div>
    ) : null,
}));

import { ChatInput } from '../chat-input';
import { ASSISTANT_CTX, useChatStore } from '@/chat/store';
import { applyConversationModelInfoToStore } from '@/chat/utils/apply-conversation-model-info';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import { chatSlots } from '@/config';
import { useAuthStore } from '@/lib/store/auth-store';
import { DRAFT_SAVE_DEBOUNCE_MS } from '@/chat/hooks/use-composer-draft';
import { loadDraft, saveDraft } from '@/chat/utils/composer-draft';
import { allowChatDraftWrites, discardAllChatDrafts } from '@/lib/storage/chat-drafts';
import type { AttachmentRef } from '@/chat/types';
import { MENTION_PAD_CLOSE, MENTION_PAD_OPEN, insertMention } from '@/chat/utils/node-mentions';

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
  org.value = { org: null, logoUrl: null };
  org.calls = 0;
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

/** The + menu opens from the keyboard the way a person tabbing to it would. */
function openPlusMenu() {
  fireEvent.keyDown(screen.getByRole('button', { name: 'Attach files and capabilities' }), { key: 'Enter' });
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

    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    type('What changed in the Q3 plan?');
    expect(sendButton().getAttribute('aria-disabled')).toBe('false');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('What changed in the Q3 plan?', undefined, undefined);
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
    expect(onSend).toHaveBeenCalledWith('Line one', undefined, undefined);
  });

  it('does not send a message made only of spaces', () => {
    const { onSend } = renderInput();
    type('   ');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
  });

  it('paints Send soft gray while empty and the brand solid once there is something to send', () => {
    renderInput();
    expect(sendButton().className).toContain('rt-variant-soft');
    expect(sendButton().getAttribute('data-accent-color')).toBe('gray');
    // The soft variant's focus outline reads --accent-8: it is pointed at the product focus ring.
    expect(sendButton().style.getPropertyValue('--accent-8')).toBe('var(--focus-ring)');
    expect(sendButton().style.backgroundColor).toBe('');

    type('Ready to go');
    expect(sendButton().className).toContain('rt-variant-solid');
    expect(sendButton().getAttribute('data-accent-color')).not.toBe('gray');
    expect(sendButton().style.backgroundColor).toBe('');
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

  it('marks Stop aria-disabled once stopping has begun so a second click cannot cancel twice', () => {
    startStreamingSlot({ stopping: true });
    renderInput();

    const stop = screen.getByRole('button', { name: 'Stop generating' }) as HTMLButtonElement;
    // Never the disabled attribute: a disabled button would drop keyboard focus to the page.
    expect(stop.disabled).toBe(false);
    expect(stop.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(stop);
    expect(streaming.cancelStreamForSlot).not.toHaveBeenCalled();
  });

  it('draws Stop as the outlined square glyph on the solid brand button', () => {
    startStreamingSlot();
    renderInput();

    const stop = screen.getByRole('button', { name: 'Stop generating' });
    expect(stop.className).toContain('rt-variant-solid');
    expect(stop.style.backgroundColor).toBe('');
    const glyph = stop.querySelector('svg');
    expect(glyph?.getAttribute('aria-hidden')).toBe('true');
    expect(glyph?.querySelector('path')?.getAttribute('d')).toBe('M6.66667 1.33333V6.66667H1.33333V1.33333H6.66667ZM8 0H0V8H8V0Z');
  });

  it('Stop is the same 32px touch-size button as Send, 44px on a touch phone only', () => {
    startStreamingSlot();
    renderInput();
    let stop = screen.getByRole('button', { name: 'Stop generating' });
    expect(stop.className).toContain('touch-size');
    expect(stop.style.width).toBe('32px');
    cleanup();
    // A phone-width window driven by a mouse keeps the 32px button.
    device.isMobile = true;
    startStreamingSlot();
    renderInput();
    stop = screen.getByRole('button', { name: 'Stop generating' });
    expect(stop.style.minWidth).toBe('32px');
    cleanup();
    // A touch screen gets the 44px target.
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(pointer: coarse)',
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    try {
      startStreamingSlot();
      renderInput();
      stop = screen.getByRole('button', { name: 'Stop generating' });
      expect(stop.style.minWidth).toBe('44px');
      expect(stop.style.minHeight).toBe('44px');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps keyboard focus on the button when the stream ends and Stop becomes Send', () => {
    const slotId = startStreamingSlot();
    renderInput();
    const stop = screen.getByRole('button', { name: 'Stop generating' });
    stop.focus();
    fireEvent.click(stop);
    expect(streaming.cancelStreamForSlot).toHaveBeenCalledTimes(1);
    // The run reports it is stopping: Stop is aria-disabled and keeps the focus.
    act(() => {
      useChatStore.getState().updateSlot(slotId, { stopping: true });
    });
    expect(stop.getAttribute('aria-disabled')).toBe('true');
    expect((stop as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(stop);
    expect(streaming.cancelStreamForSlot).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(stop);
    act(() => {
      useChatStore.getState().updateSlot(slotId, { isStreaming: false, stopping: false });
    });
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(sendButton());
  });

  it('offers Stop for a run that kept going after a reload, and stops it', () => {
    const slotId = startStreamingSlot({ isStreaming: false, remoteRunInProgress: true, remoteRunId: 'server-run' });
    renderInput();

    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Stop generating' }));

    expect(streaming.cancelStreamForSlot).toHaveBeenCalledWith(slotId);
  });

  it('marks that Stop aria-disabled once its cancel went out', () => {
    startStreamingSlot({ isStreaming: false, remoteRunInProgress: true, remoteRunId: 'server-run', remoteStopRequested: true });
    renderInput();

    const stop = screen.getByRole('button', { name: 'Stop generating' });
    expect(stop.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(stop);
    expect(streaming.cancelStreamForSlot).not.toHaveBeenCalled();
  });

  it('keeps Send for a reloaded run it has no id to stop', () => {
    startStreamingSlot({ isStreaming: false, remoteRunInProgress: true, remoteRunId: null });
    renderInput();

    expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Send message' })).toBeTruthy();
  });

  it('the collapsed dock never shows Stop: a run on the chat page is not its run', () => {
    startStreamingSlot();
    renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });
    expect(screen.queryByTestId('chat-stop-button')).toBeNull();
    expect(screen.getByRole('button', { name: 'Send message' })).toBeTruthy();
  });

  it('keeps Send and sends while an answer streams, when sending during a run is on', () => {
    startStreamingSlot();
    const { onSend } = renderInput({ sendDuringRun: true });
    type('A note beside the answer');

    expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull();
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('A note beside the answer', undefined, undefined);

    type('And another');
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenLastCalledWith('And another', undefined, undefined);
  });

  it('queues a follow-up while the answer streams: Stop while the box is empty, Send once it holds a question', () => {
    const slotId = startStreamingSlot();
    const { onSend } = renderInput({ queueDuringRun: true });

    expect(screen.getByRole('button', { name: 'Stop generating' })).toBeTruthy();
    type('A follow-up');
    expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull();
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('A follow-up', undefined, undefined);
    expect(composer().value).toBe('');

    // The box is empty again: Stop is back, and still stops the answer.
    fireEvent.click(screen.getByRole('button', { name: 'Stop generating' }));
    expect(streaming.cancelStreamForSlot).toHaveBeenCalledWith(slotId);
  });

  it('takes a queued question back into an empty box when it is edited', () => {
    const slotId = startStreamingSlot();
    renderInput({ queueDuringRun: true });

    act(() => useChatStore.getState().updateSlot(slotId, { composerRestore: 'Edit me' }));

    expect(composer().value).toBe('Edit me');
    expect(useChatStore.getState().slots[slotId].composerRestore).toBeNull();
    expect(useChatStore.getState().slots[slotId].composerHasText).toBe(true);
  });

  it('lets a follow-up through once the stopped run is winding down', () => {
    startStreamingSlot({ stopping: true });
    const { onSend } = renderInput();
    type('Next question');

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('Next question', undefined, undefined);
  });

  it('waits for the agent tools to finish loading before it can send', () => {
    useChatStore.setState({
      settings: { ...useChatStore.getState().settings, queryMode: 'agent' },
      universalAgentToolsLoading: true,
    });
    const { onSend } = renderInput();
    type('Book a meeting');

    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('keeps what was typed while a send was being confirmed, and drops only the sent words', async () => {
    const accepted = deferred<boolean>();
    renderInput({ onSend: vi.fn(() => accepted.promise) });
    type('First question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    type('First question and a second one');
    await act(async () => accepted.resolve(true));

    expect(composer().value).toBe('and a second one');
  });

  it('leaves a box rewritten while a send was being confirmed as it is', async () => {
    const accepted = deferred<boolean>();
    renderInput({ onSend: vi.fn(() => accepted.promise) });
    type('First question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    type('Something else entirely');
    await act(async () => accepted.resolve(true));

    expect(composer().value).toBe('Something else entirely');
  });

  it('clears the box once a confirmed send lands, when resolving the send rewrote it (a typed name became its chip)', async () => {
    const accepted = deferred<boolean>();
    // A rich field: its handle reads the box itself, and finishing a typed alias rewrites the box and reports it.
    const box = { text: '', onChange: (_t: string) => {} };
    const RichField = React.forwardRef<unknown, { value: string; onChange(t: string): void; onKeyDown(e: React.KeyboardEvent): void; placeholder: string }>(
      function RichField(props, ref) {
        box.onChange = props.onChange;
        React.useImperativeHandle(ref, () => ({
          focus: () => {},
          clear: () => { box.text = ''; props.onChange(''); },
          setText: (t: string) => { box.text = t; props.onChange(t); },
          insertText: () => {},
          getValue: () => ({ text: box.text }),
        }));
        return (
          <textarea
            placeholder={props.placeholder}
            value={props.value}
            onKeyDown={props.onKeyDown}
            onChange={(e) => { box.text = e.target.value; props.onChange(e.target.value); }}
          />
        );
      },
    );
    const plain = chatSlots.useComposerInput;
    const input = vi.spyOn(chatSlots, 'useComposerInput').mockImplementation((opts) => ({
      ...plain(opts),
      resolveSend: () => {
        box.text = 'PipesHub, ask the team';
        box.onChange(box.text);
        return { text: box.text };
      },
    }));
    const slots = chatSlots as { ComposerField: unknown };
    const ownField = slots.ComposerField;
    slots.ComposerField = RichField;
    try {
      const onSend = vi.fn(() => accepted.promise);
      renderInput({ onSend });
      type('assistant, ask the team');
      fireEvent.keyDown(composer(), { key: 'Enter' });
      expect(onSend).toHaveBeenCalledWith('PipesHub, ask the team', undefined, undefined);

      await act(async () => accepted.resolve(true));

      expect(composer().value).toBe('');
    } finally {
      input.mockRestore();
      slots.ComposerField = ownField;
    }
  });

  it('clears the box once a confirmed send lands on an untouched draft', async () => {
    const accepted = deferred<boolean>();
    renderInput({ onSend: vi.fn(() => accepted.promise) });
    type('First question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    await act(async () => accepted.resolve(true));

    expect(composer().value).toBe('');
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
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(onSend).not.toHaveBeenCalled();

    await act(async () => upload.resolve(ref('rec-1')));
    expect(screen.queryByLabelText('Uploading report.pdf')).toBeNull();
    expect(screen.getByText('2 KB')).toBeTruthy();

    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('Summarise the attached report', [ref('rec-1')], undefined);
    expect(screen.queryByText('report.pdf')).toBeNull();
  });

  it('can send an attachment without any typed text', async () => {
    const { onSend } = renderInput({ onUploadFile: vi.fn(async () => ref('rec-1')) });

    await act(async () => pick(pdf()));
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('', [ref('rec-1')], undefined);
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
    expect(toastTitles()).toEqual([reason]);

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

    expect(onSend).toHaveBeenCalledWith('Compare these', [ref('rec-ok')], undefined);
  });

  it('keeps a failed attachment and its retry when there is nothing else to send', async () => {
    const onUploadFile = vi.fn(async (): Promise<AttachmentRef> => {
      throw new Error('The file server is busy. Try again in a minute.');
    });
    const { onSend } = renderInput({ onUploadFile });

    await act(async () => pick(pdf()));
    expect(sendButton().getAttribute('aria-disabled')).toBe('true');
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

    openPlusMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach files' }));

    expect(screen.getByText('Upload your file')).toBeTruthy();
    expect(screen.getByText('Up to 10 files per message')).toBeTruthy();
    expect(
      screen.getByText('Supports: PDF, PNG, JPEG, JPG, TXT, MD, DOCX, XLSX, CSV.'),
    ).toBeTruthy();
  });

  it('closes the attach menu once files are picked so the new chips are visible', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    const { container } = renderInput({ onUploadFile });

    openPlusMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach files' }));
    expect(screen.getByText('Upload your file')).toBeTruthy();

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [pdf('picked.pdf')] } });

    expect(screen.getByText('picked.pdf')).toBeTruthy();
    expect(screen.queryByText('Upload your file')).toBeNull();
    expect(screen.queryByText('Attach files')).toBeNull();
  });

  it('closes the upload area even when every picked file is turned away', () => {
    const { container } = renderInput({ onUploadFile: vi.fn() });
    openPlusMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Attach files' }));

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'archive.zip', { type: 'application/zip' })] },
    });

    expect(screen.queryByText('Upload your file')).toBeNull();
  });
});

/** A reply that holds a question card: Regenerate opens the bar for it instead of re-running at once. */
function holdQuestionCard(slotId: string, messageId = 'msg-7'): void {
  useChatStore.getState().updateSlot(slotId, {
    messages: [
      {
        id: messageId,
        role: 'assistant',
        content: [{ type: 'text', text: 'Which region?' }],
        metadata: { custom: { messageId, persistedAskUserQuestion: { name: 'ask_user_question', questions: [] } } },
      } as never,
    ],
  });
}

describe('ChatInput — regenerate and edit a previous message', () => {
  function openActiveConversation(): string {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    return slotId;
  }

  it('regenerates the chosen answer with the filters it was first asked with', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    holdQuestionCard(slotId);
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

  it("re-runs a question card's reply with the answers picked again on the card", () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    holdQuestionCard(slotId);
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Plan it' });
    });
    expect(useChatStore.getState().regenTargetMessageId).toBe('msg-7');

    const message = 'User selections:\n1. "Which region?" → US';
    act(() => useChatStore.getState().setRegenAnswers({ messageId: 'msg-7', payload: { name: 'ask_user_question', questions: [] }, message, answers: {} }));
    fireEvent.click(sendButton());

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(slotId, 'msg-7', undefined, undefined, undefined, message);
    expect(useChatStore.getState().regenTargetMessageId).toBeNull();
  });

  it('regenerates when the reopened card is submitted', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.setActiveSlot(slotId);
    holdQuestionCard(slotId);
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Plan it' });
    });

    act(() => {
      useCommandStore.getState().dispatch('runRegenerate');
    });

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledTimes(1);
    expect(streaming.streamRegenerateForSlot.mock.calls[0][1]).toBe('msg-7');
  });

  it('re-runs a plain answer at once from one click, with its first filters and no bar (DF-094)', () => {
    const slotId = openActiveConversation();
    const { onSend } = renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', {
        messageId: 'msg-7',
        text: 'Which vendors renewed?',
        appliedFilters: { apps: [{ id: 'app-drive', name: 'Drive', nodeType: 'app', connector: 'DRIVE' }], kb: [] },
      });
    });

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledTimes(1);
    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(slotId, 'msg-7', undefined, { apps: ['app-drive'], kb: [] });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryByText('Regenerate response')).toBeNull();
    expect(composer().value).toBe('');
  });

  it('does not start a second run while the answer is still being written', () => {
    const slotId = openActiveConversation();
    useChatStore.getState().updateSlot(slotId, { isStreaming: true });
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Plan it' });
    });
    expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();
  });

  it('forgets answers picked on the card when the bar is closed', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Plan it' });
      useChatStore.getState().setRegenAnswers({ messageId: 'msg-7', payload: { name: 'ask_user_question', questions: [] }, message: 'User selections:', answers: {} });
    });

    fireEvent.keyDown(composer(), { key: 'Escape' });

    expect(useChatStore.getState().regenAnswers).toBeNull();
    expect(useChatStore.getState().regenTargetMessageId).toBeNull();
  });

  it('regenerates on the slot the answer names, not the active one', () => {
    const store = useChatStore.getState();
    const active = store.createSlot('conv-1');
    const beside = store.createSlot('conv-2');
    store.setActiveSlot(active);
    holdQuestionCard(beside, 'msg-9');
    renderInput();

    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-9', text: 'Old question', slotId: beside });
    });
    fireEvent.click(sendButton());

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(beside, 'msg-9', undefined, undefined);
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
    expect(screen.queryByText('Edit query')).toBeNull();
  });

  it('re-asks with the edited question when the user sends an edit', () => {
    const slotId = openActiveConversation();
    const { onSend } = renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    expect(screen.getByText('Edit query')).toBeTruthy();
    expect(composer().value).toBe('Original question');
    expect(composer().readOnly).toBe(false);

    type('Edited question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledTimes(1);
    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
      slotId, 'msg-3', undefined, undefined, { query: 'Edited question' },
    );
    expect(composer().value).toBe('');
    expect(screen.queryByText('Edit query')).toBeNull();
  });

  it('re-asks an edit on the slot the answer names (an agent thread), not the room on screen', () => {
    const room = openActiveConversation();
    const thread = useChatStore.getState().createSlot('thread-1');
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-9', text: 'Agent question', slotId: thread });
    });

    type('Edited agent question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledTimes(1);
    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
      thread, 'msg-9', undefined, undefined, { query: 'Edited agent question' },
    );
    expect(streaming.streamRegenerateForSlot.mock.calls[0][0]).not.toBe(room);
  });

  it('regenerates without a new question when the edit leaves the text unchanged', () => {
    const slotId = openActiveConversation();
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(slotId, 'msg-3', undefined, undefined);
  });

  it('keeps the original filters when editing a question that was scoped', () => {
    const slotId = openActiveConversation();
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', {
        messageId: 'msg-3',
        text: 'Original question',
        appliedFilters: { apps: [{ id: 'app-drive', name: 'Drive' }], kb: [{ id: 'kb-legal', name: 'Legal' }] },
      });
    });

    type('Edited question');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
      slotId, 'msg-3', undefined, { apps: ['app-drive'], kb: ['kb-legal'] }, { query: 'Edited question' },
    );
  });

  it('Edit: a source the question named comes back as its chip, and the scope stays', () => {
    const slotId = openActiveConversation();
    renderInput();
    const folder = { id: 'fol-1', name: 'Orbit folder', nodeType: 'folder', connector: 'KB', mentioned: true };
    const app = { id: 'app-drive', name: 'Drive', nodeType: 'app', connector: 'DRIVE', mentioned: false };
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', {
        messageId: 'msg-3',
        text: 'How many crates in Orbit folder? Ask Drive too',
        appliedFilters: { apps: [app], kb: [], records: [folder] },
      });
    });

    // Only what the question named is a chip: a source picked for the chat is not in its words.
    expect(composer().value).toBe(`How many crates in ${MENTION_PAD_OPEN}Orbit folder${MENTION_PAD_CLOSE}? Ask Drive too`);
    expect(mentionField.latest && (mentionField.latest as { mentions?: { id: string }[] }).mentions?.map((node) => node.id)).toEqual(['fol-1']);

    // Sent as it was: the words come back whole, so it is a plain regenerate with the question's own scope.
    fireEvent.keyDown(composer(), { key: 'Enter' });
    expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
      slotId, 'msg-3', undefined, { apps: ['app-drive'], kb: [], records: ['fol-1'] },
    );
  });

  it('turns @ mentions off while editing, so the question keeps the filters it was asked with', () => {
    const mentionsOff = () => mentionField.rendered.mock.calls.at(-1)?.[0];
    renderInput();
    expect(mentionsOff()).toBe(false);

    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    expect(mentionsOff()).toBe(true);
    fireEvent.keyDown(composer(), { key: 'Escape' });
    expect(mentionsOff()).toBe(false);
  });

  describe('the files of the edited question', () => {
    const stored = [ref('rec-a'), ref('rec-b')];

    function startEdit(attachments: AttachmentRef[] = stored) {
      act(() => {
        useCommandStore.getState().dispatch('showEditQuery', {
          messageId: 'msg-3',
          text: 'Original question',
          attachments,
        });
      });
    }
    const chip = (name: string) => screen.queryByText(name)

    it('shows the files stored with the question as chips', () => {
      openActiveConversation();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });

      startEdit();

      expect(chip('rec-a.pdf')).toBeTruthy();
      expect(chip('rec-b.pdf')).toBeTruthy();
      expect(screen.getAllByText('PDF')).toHaveLength(2);
    });

    it('sends only the text when the files are left as they are', () => {
      const slotId = openActiveConversation();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });
      startEdit();

      type('Edited question');
      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
        slotId, 'msg-3', undefined, undefined, { query: 'Edited question' },
      );
    });

    it('regenerates as it was when neither the text nor the files changed', () => {
      const slotId = openActiveConversation();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });
      startEdit();

      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(slotId, 'msg-3', undefined, undefined);
    });

    it('detaches a removed file from the question without deleting the stored file', () => {
      const slotId = openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile });
      startEdit();

      fireEvent.click(screen.getByRole('button', { name: 'Remove rec-a.pdf' }));
      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(onDeleteFile).not.toHaveBeenCalled();
      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
        slotId, 'msg-3', undefined, undefined, { query: 'Original question', attachments: [ref('rec-b')] },
      );
    });

    it('sends an empty list when every file was removed', () => {
      const slotId = openActiveConversation();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });
      startEdit();

      fireEvent.click(screen.getByRole('button', { name: 'Remove rec-a.pdf' }));
      fireEvent.click(screen.getByRole('button', { name: 'Remove rec-b.pdf' }));
      type('Edited question');
      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
        slotId, 'msg-3', undefined, undefined, { query: 'Edited question', attachments: [] },
      );
    });

    it('sends the stored files together with a file added in the edit', async () => {
      const slotId = openActiveConversation();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-c')), onDeleteFile: vi.fn() });
      startEdit();

      await act(async () => pick(pdf()));
      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledWith(
        slotId, 'msg-3', undefined, undefined,
        { query: 'Original question', attachments: [ref('rec-a'), ref('rec-b'), ref('rec-c')] },
      );
    });

    it('counts the stored files toward the limit of ten', () => {
      openActiveConversation();
      const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
      renderInput({ onUploadFile, onDeleteFile: vi.fn() });
      startEdit(Array.from({ length: 9 }, (_, i) => ref(`rec-${i}`)));

      pick(pdf('one.pdf'), pdf('two.pdf'))

      expect(onUploadFile).toHaveBeenCalledTimes(1);
      expect(toastTitles()).toEqual(['Maximum 10 attachments per message.']);
    });

    it('waits for a file that is still uploading before it sends the edit', async () => {
      openActiveConversation();
      const upload = deferred<AttachmentRef>();
      renderInput({ onUploadFile: vi.fn(() => upload.promise), onDeleteFile: vi.fn() });
      startEdit();
      pick(pdf());

      fireEvent.keyDown(composer(), { key: 'Enter' });
      expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();

      await act(async () => upload.resolve(ref('rec-c')));
      fireEvent.keyDown(composer(), { key: 'Enter' });
      expect(streaming.streamRegenerateForSlot).toHaveBeenCalledTimes(1);
    });

    it('drops the files picked in an edit that is cancelled, and keeps the stored ones', async () => {
      openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-c')), onDeleteFile });
      startEdit();
      await act(async () => pick(pdf()));

      fireEvent.keyDown(composer(), { key: 'Escape' });

      expect(onDeleteFile).toHaveBeenCalledTimes(1);
      expect(onDeleteFile).toHaveBeenCalledWith('rec-c');
      expect(chip('rec-a.pdf')).toBeNull();
      expect(chip('report.pdf')).toBeNull();
    });

    it('puts back the files the reader had attached when the edit is cancelled', async () => {
      openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-mine')), onDeleteFile });
      await act(async () => pick(pdf('mine.pdf')));
      expect(chip('mine.pdf')).toBeTruthy();

      startEdit();
      expect(chip('mine.pdf')).toBeNull();
      expect(chip('rec-a.pdf')).toBeTruthy();
      fireEvent.keyDown(composer(), { key: 'Escape' });

      expect(chip('mine.pdf')).toBeTruthy();
      expect(chip('rec-a.pdf')).toBeNull();
      expect(onDeleteFile).not.toHaveBeenCalled();
    });

    it('puts back the files the reader had attached once the edit is sent', async () => {
      openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-mine')), onDeleteFile });
      await act(async () => pick(pdf('mine.pdf')));

      startEdit();
      type('Edited question');
      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(chip('mine.pdf')).toBeTruthy();
      expect(chip('rec-a.pdf')).toBeNull();
      expect(onDeleteFile).not.toHaveBeenCalled();
    });

    it('keeps a file picked for a sent edit, since the question now holds it', async () => {
      openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-c')), onDeleteFile });
      startEdit();
      await act(async () => pick(pdf()));

      fireEvent.keyDown(composer(), { key: 'Enter' });

      expect(onDeleteFile).not.toHaveBeenCalled();
    });

    it('leaves the edit, and its files, when the reader opens another conversation', async () => {
      openActiveConversation();
      const other = useChatStore.getState().createSlot('conv-2');
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-c')), onDeleteFile });
      startEdit();
      await act(async () => pick(pdf()));

      act(() => useChatStore.getState().setActiveSlot(other));

      expect(screen.queryByText('Edit query')).toBeNull();
      expect(chip('rec-a.pdf')).toBeNull();
      expect(chip('report.pdf')).toBeNull();
      expect(onDeleteFile).toHaveBeenCalledWith('rec-c');
    });

    it('leaves the edit files when a regenerate starts instead', () => {
      holdQuestionCard(openActiveConversation());
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });
      startEdit();

      act(() => {
        useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Old question' });
      });

      expect(screen.getByText('Regenerate response')).toBeTruthy();
      expect(chip('rec-a.pdf')).toBeNull();
    });

    it('shows the files of the newer edit when the reader switches edits, and drops the earlier new ones', async () => {
      openActiveConversation();
      const onDeleteFile = vi.fn();
      renderInput({ onUploadFile: vi.fn(async () => ref('rec-c')), onDeleteFile });
      startEdit();
      await act(async () => pick(pdf()));

      startEdit([ref('rec-z')]);

      expect(onDeleteFile).toHaveBeenCalledWith('rec-c');
      expect(onDeleteFile).not.toHaveBeenCalledWith('rec-a');
      expect(chip('rec-z.pdf')).toBeTruthy();
      expect(chip('rec-a.pdf')).toBeNull();
      expect(chip('report.pdf')).toBeNull();
    });

    it('does not keep the question files after the edit when the question had none', () => {
      openActiveConversation();
      renderInput({ onUploadFile: vi.fn(), onDeleteFile: vi.fn() });

      startEdit([]);
      fireEvent.keyDown(composer(), { key: 'Escape' });

      expect(screen.queryByText('Edit query')).toBeNull();
    });
  });

  it('does not send an edit that was cleared, and stays in edit mode', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    type('   ');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();
    expect(screen.getByText('Edit query')).toBeTruthy();
  });

  it('leaves edit mode on Escape without re-asking', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });

    fireEvent.keyDown(composer(), { key: 'Escape' });

    expect(screen.queryByText('Edit query')).toBeNull();
    expect(composer().value).toBe('');
    expect(streaming.streamRegenerateForSlot).not.toHaveBeenCalled();
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

    expect(onSend).toHaveBeenCalledWith('Find my tickets', undefined, undefined);
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

describe('ChatInput — @ in agent and project chats', () => {
  const projectScope = (knowledgeDefaults: { apps: string[]; kb: string[]; records?: string[] }) => ({
    projectId: 'p1',
    connectors: [],
    knowledgeCollectionRows: [],
    knowledgeDefaults,
    linkedKnowledgeBaseId: 'kb-uploads',
    toolGroups: [],
    mcpGroups: [],
    toolCatalogFullNames: [],
  });

  // @ is off in both: their sources can't be searched in one request. The scope's sources are still worked out for a
  // field that offers @ there.
  it('offers no @ in a project chat; the project’s own sources come first where their names are known', () => {
    useChatStore.setState({
      projectScope: projectScope({ apps: ['app-1'], kb: ['kb-1'] }),
      collectionNamesCache: { 'app-1': 'Jira' },
      collectionMetaCache: { 'app-1': { name: 'Jira', nodeType: 'app', connector: 'JIRA' } },
    });
    renderInput();
    const [off, context] = mentionField.rendered.mock.calls.at(-1) ?? [];
    expect(off).toBe(true);
    expect(context?.preferredSources).toEqual([expect.objectContaining({ id: 'app-1', name: 'Jira', nodeType: 'app' })]);
  });

  it('offers no @ in an agent chat; the agent’s knowledge comes first', () => {
    useChatStore.setState({
      agentKnowledgeDefaults: { apps: [], kb: ['kb-9'] },
      collectionNamesCache: { 'kb-9': 'Handbook' },
    });
    renderInput({ isAgentChat: true, agentId: 'agent-1' });
    const [off, context] = mentionField.rendered.mock.calls.at(-1) ?? [];
    expect(off).toBe(true);
    expect(context?.preferredSources).toEqual([expect.objectContaining({ id: 'kb-9', name: 'Handbook' })]);
  });

  it('keeps @ outside agent and project chats, with every source', () => {
    renderInput();
    const [off, context] = mentionField.rendered.mock.calls.at(-1) ?? [];
    expect(off).toBeFalsy();
    expect(context?.preferredSources).toBeNull();
  });
});

describe('ChatInput — sources named with @', () => {
  const report = { id: 'q3', name: 'Q3 Report', nodeType: 'record', connector: 'KB' };
  const nameSources = (nodes: (typeof report)[], tail = 'what changed') => {
    const text = nodes.reduce((acc, node) => insertMention(acc, acc.length, acc.length, node), '');
    act(() => {
      mentionField.latest?.onChange(`${text}${tail}`);
      mentionField.latest?.onMentionsChange(nodes);
    });
  };

  it('sends the named source with that question only: the chat’s own selection stays as it was', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, filters: { apps: ['app-drive'], kb: [] } } });
    const { onSend } = renderInput();
    nameSources([report]);
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledWith('Q3 Report what changed', undefined, [expect.objectContaining({ id: 'q3' })]);
    expect(useChatStore.getState().settings.filters).toEqual({ apps: ['app-drive'], kb: [] });
    // Its name is kept, so the sent question shows it as a chip.
    expect(useChatStore.getState().collectionMetaCache.q3).toMatchObject({ name: 'Q3 Report', nodeType: 'record' });
  });

  it('two sources of one name after the same word typed plain, and one chip deleted: the list settles, and sends', () => {
    const [a, b] = ['a', 'b'].map((id) => ({ ...report, id, name: 'Docs' }));
    const chip = (node: typeof report) => insertMention('', 0, 0, node);
    const { onSend } = renderInput();
    act(() => {
      mentionField.latest?.onChange(`Docs and ${chip(a)}${chip(b)}`);
      mentionField.latest?.onMentionsChange([a, b]);
    });
    // Which node owns which "Docs" is now ambiguous: the kept list must not swap order on every render.
    act(() => mentionField.latest?.onChange(`Docs and ${chip(a)}what changed`));
    fireEvent.click(sendButton());
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('refuses a message naming more than ten sources, and says why', () => {
    const { onSend } = renderInput();
    nameSources(Array.from({ length: 11 }, (_, i) => ({ ...report, id: `r${i}`, name: `Report ${i}` })));
    fireEvent.click(sendButton());
    expect(onSend).not.toHaveBeenCalled();
    expect(toastTitles()[0]).toContain('10 sources');
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

  it('leaves the larger view behind when the composer that opened it goes away', () => {
    const { unmount } = renderInput();
    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    fireEvent.click(screen.getByText('Toggle panel size'));
    expect(useChatStore.getState().expansionViewMode).toBe('overlay');

    unmount();

    expect(useChatStore.getState().expansionViewMode).toBe('inline');
  });

  it('opens the agent resources panel for universal agent mode', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, queryMode: 'agent' } });
    renderInput();

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);

    expect(screen.getByText('Universal agent resources')).toBeTruthy();
  });

  it('keeps the agent resources panel after a chat stored in an older mode was opened', () => {
    applyConversationModelInfoToStore({ modelKey: 'k', modelName: 'm', chatMode: 'internal_search' }, ASSISTANT_CTX);
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

  it('keeps the panel open for a click inside a dialog opened from it', () => {
    renderInput();
    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    // Like an MCP server's key dialog: portaled to the body, outside the composer.
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    const field = document.createElement('input');
    dialog.appendChild(field);
    document.body.appendChild(dialog);

    fireEvent.mouseDown(field);

    expect(screen.getByText('Choose the handbook')).toBeTruthy();
    dialog.remove();
  });
});

describe('ChatInput — placeholder', () => {
  const field = (text: string) => screen.getByPlaceholderText(text) as HTMLTextAreaElement;

  it('names the workspace in a solo chat once its name is known', async () => {
    org.value = { org: { shortName: 'Acme', registeredName: 'Acme Corp' }, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true });
    expect(field('Ask anything...')).toBeTruthy();
    await act(async () => {});
    expect(field('Ask or search anything on Acme...')).toBeTruthy();
  });

  it('falls back to the registered name, and to "Ask anything..." while no name is known', async () => {
    org.value = { org: { shortName: '', registeredName: 'Acme Corp' }, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true });
    await act(async () => {});
    expect(field('Ask or search anything on Acme Corp...')).toBeTruthy();
    cleanup();
    org.value = { org: null, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true });
    await act(async () => {});
    expect(field('Ask anything...')).toBeTruthy();
  });

  it('keeps the given text, the agent chat text and the text of composers that do not ask for the workspace', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true, placeholder: 'Message the chat, or @PipesHub to ask' });
    await act(async () => {});
    expect(field('Message the chat, or @PipesHub to ask')).toBeTruthy();
    cleanup();
    renderInput({ useWorkspacePlaceholder: true, isAgentChat: true, agentId: 'agent-1' });
    await act(async () => {});
    expect(field('Ask anything...')).toBeTruthy();
    cleanup();
    renderInput();
    await act(async () => {});
    expect(field('Ask anything...')).toBeTruthy();
    // None of them needs the name, so none asks for it.
    expect(org.calls).toBe(0);
  });

  it('asks about the paste while it waits in an empty box, and goes back once there is text', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true, onUploadFile: vi.fn(() => new Promise<AttachmentRef>(() => {})) });
    await act(async () => {});
    const longText = Array.from({ length: 200 }, (_, i) => `Line ${i}`).join('\n');
    fireEvent.paste(field('Ask or search anything on Acme...'), { clipboardData: { items: [], getData: () => longText } });
    expect(field('Ask about the pasted content...')).toBeTruthy();
    fireEvent.change(field('Ask about the pasted content...'), { target: { value: 'Summarise it' } });
    expect(field('Ask or search anything on Acme...')).toBeTruthy();
  });

  const longPaste = () => ({ clipboardData: { items: [], getData: () => Array.from({ length: 200 }, (_, i) => `Line ${i}`).join('\n') } });
  const pendingUpload = () => vi.fn(() => new Promise<AttachmentRef>(() => {}));

  it('edit mode never switches to the paste text and keeps the field text', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    renderInput({ useWorkspacePlaceholder: true, onUploadFile: pendingUpload() });
    await act(async () => {});
    fireEvent.paste(field('Ask or search anything on Acme...'), longPaste());
    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });
    fireEvent.change(screen.getByDisplayValue('Original question'), { target: { value: '' } });
    expect(screen.queryByPlaceholderText('Ask about the pasted content...')).toBeNull();
    expect(field('Ask or search anything on Acme...')).toBeTruthy();
  });

  it('regenerate mode never switches to the paste text', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    const store = useChatStore.getState();
    const slotId = store.createSlot('conv-1');
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    holdQuestionCard(slotId);
    renderInput({ useWorkspacePlaceholder: true, onUploadFile: pendingUpload() });
    await act(async () => {});
    fireEvent.paste(field('Ask or search anything on Acme...'), longPaste());
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Plan it' });
    });
    expect(screen.queryByPlaceholderText('Ask about the pasted content...')).toBeNull();
  });

  it('the dimmed field under the overlay panel keeps the base text, never the paste text', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({ useWorkspacePlaceholder: true, onUploadFile: pendingUpload() });
    await act(async () => {});
    fireEvent.paste(field('Ask or search anything on Acme...'), longPaste());
    expect(field('Ask about the pasted content...')).toBeTruthy();

    fireEvent.click(screen.getByText('apps').closest('button') as HTMLButtonElement);
    fireEvent.click(screen.getByText('Toggle panel size'));
    expect(screen.getByRole('dialog', { name: 'Expanded panel' })).toBeTruthy();
    expect(screen.queryByPlaceholderText('Ask about the pasted content...')).toBeNull();
    expect(field('Ask or search anything on Acme...')).toBeTruthy();
  });

  it('the collapsed KB dock keeps its own text and never asks for the workspace', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({
      variant: 'widget',
      expandable: true,
      widgetPlaceholder: 'Ask in Handbook',
      placeholder: 'Ask anything in Handbook...',
    });
    await act(async () => {});
    expect(field('Ask in Handbook')).toBeTruthy();
    expect(screen.queryByPlaceholderText(/Acme|pasted/)).toBeNull();
    expect(org.calls).toBe(0);
  });

  it('the expanded KB dock keeps its own text, with a paste too', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({
      variant: 'widget',
      expandable: true,
      widgetPlaceholder: 'Ask in Handbook',
      placeholder: 'Ask anything in Handbook...',
      onUploadFile: pendingUpload(),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Expand composer' }));
    await act(async () => {});
    fireEvent.paste(field('Ask anything in Handbook...'), longPaste());
    expect(field('Ask anything in Handbook...')).toBeTruthy();
    expect(screen.queryByPlaceholderText(/Acme|pasted/)).toBeNull();
    expect(org.calls).toBe(0);
  });

  it('Projects (no workspace prop) stays "Ask anything...", with a paste too', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    renderInput({ onUploadFile: pendingUpload() });
    await act(async () => {});
    fireEvent.paste(field('Ask anything...'), longPaste());
    expect(field('Ask anything...')).toBeTruthy();
    expect(screen.queryByPlaceholderText(/Acme|pasted/)).toBeNull();
    expect(org.calls).toBe(0);
  });

  it('says it is listening while dictating, whatever else is in the box', async () => {
    org.value = { org: { shortName: 'Acme' }, logoUrl: null };
    speech.state = { ...speech.state, isListening: true };
    renderInput({ useWorkspacePlaceholder: true });
    await act(async () => {});
    expect(field('Listening...')).toBeTruthy();
  });
});

describe('ChatInput — the apps button dot', () => {
  const appsButton = () => screen.getByText('apps').closest('button') as HTMLButtonElement;
  const dot = () => appsButton().querySelector('[aria-hidden]');
  const set = (patch: Record<string, unknown>) =>
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, ...patch } });

  it('has no dot with nothing narrowed', () => {
    renderInput();
    expect(dot()).toBeNull();
  });

  it('has no dot when the selected sources already show as chips', () => {
    set({ filters: { apps: [], kb: ['kb-handbook'] } });
    renderInput();
    expect(screen.getByRole('group', { name: 'Attachments' })).toBeTruthy();
    expect(dot()).toBeNull();
  });

  it('has no dot in agent mode when only sources are narrowed, shown as chips', () => {
    set({ queryMode: 'agent', filters: { apps: [], kb: ['kb-handbook'] } });
    useChatStore.setState({ universalAgentStreamTools: null });
    renderInput();
    expect(screen.getByRole('group', { name: 'Attachments' })).toBeTruthy();
    expect(dot()).toBeNull();
  });

  it('keeps the dot when tools are narrowed, which no chip shows', () => {
    set({ queryMode: 'agent' });
    useChatStore.setState({ universalAgentStreamTools: ['jira-a:jira.search'] });
    renderInput();
    expect(screen.queryByRole('group', { name: 'Attachments' })).toBeNull();
    expect(dot()).not.toBeNull();
  });

  it('keeps the dot for narrowed tools even with source chips showing', () => {
    set({ queryMode: 'agent', filters: { apps: [], kb: ['kb-handbook'] } });
    useChatStore.setState({ universalAgentStreamTools: ['jira-a:jira.search'] });
    renderInput();
    expect(dot()).not.toBeNull();
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

  it('draws the model chip as a ghost Radix button that opens its panel and names the effort it runs with', () => {
    useChatStore.setState({
      settings: {
        ...useChatStore.getState().settings,
        defaultModels: {
          __assistant__: { modelKey: 'k-smart', modelName: 'smart-1', modelFriendlyName: 'Smart One', modelProvider: 'anthropic' },
        },
        availableModels: {
          __assistant__: { models: [{ modelKey: 'k-smart', modelName: 'smart-1', isReasoning: true } as never], fetchedAt: Date.now() },
        },
      },
    });
    renderInput();
    const chip = screen.getByRole('button', { name: 'Model: Smart One, High reasoning effort. Open configured models.' });
    expect(chip.className).toContain('rt-Button');
    expect(chip.className).toContain('rt-variant-ghost');
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    expect(chip.textContent).toContain('High');
    expect(chip.textContent).not.toContain('Default');

    fireEvent.click(chip);
    expect(chip.getAttribute('aria-expanded')).toBe('true');
    expect(chip.className).toContain('rt-variant-soft');

    // Escape closes the panel and hands focus back to the chip.
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('Pick Fast One')).toBeNull();
    expect(document.activeElement).toBe(chip);
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

  it("says why dictation was refused (402), in the server's words", () => {
    renderInput();

    act(() => speech.onError?.('Request failed', 'This request was refused.'));

    expect(toastTitles()).toEqual(['This request was refused.']);
  });

  it('explains why voice input is off when no speech model is set up', () => {
    speech.state = { ...speech.state, isSupported: false, unavailableReason: 'stt-not-configured' };
    renderInput();

    const mic = screen.getByRole('button', {
      name: 'Configure a Speech-to-Text (STT) model in AI models settings to enable voice input.',
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

    expect(onSend).toHaveBeenCalledWith('What is this doc about?', undefined, undefined);
  });

  it('expands into the full composer when the user asks for more', () => {
    renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });

    fireEvent.click(screen.getByRole('button', { name: 'Expand composer' }));

    expect(document.activeElement).toBe(composer());
  });

  it('on a phone, expands when the tap lands in the row around the one-line field', () => {
    device.isMobile = true;
    renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });
    const row = screen.getByRole('button', { name: 'Expand composer' }).parentElement as HTMLElement;

    fireEvent.click(row);

    expect(document.activeElement).toBe(composer());
  });

  it('sends a new question while a chat left on the chat page is still answering', () => {
    startStreamingSlot();
    const { onSend } = renderInput({ variant: 'widget' });
    fireEvent.change(screen.getByPlaceholderText('Ask anything...'), { target: { value: 'Hi' } });

    expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull();
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('Hi', undefined, undefined);
    expect(streaming.cancelStreamForSlot).not.toHaveBeenCalled();
  });

  it('has a named send button in the widget too', () => {
    const { onSend } = renderInput({ variant: 'widget' });
    fireEvent.change(screen.getByPlaceholderText('Ask anything...'), { target: { value: 'Hi' } });

    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('Hi', undefined, undefined);
  });
});

describe('ChatInput — the Collections and All records dock, once open, is the chat composer', () => {
  const openDock = () => {
    const utils = renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });
    fireEvent.click(screen.getByRole('button', { name: 'Expand composer' }));
    return utils;
  };
  const card = (root: HTMLElement) => root.querySelector('[data-composer-card]') as HTMLElement;
  const surface = (el: HTMLElement) => ({
    background: el.style.background,
    backdropFilter: el.style.backdropFilter,
    border: el.style.border,
  });
  const appsButton = () => screen.getByText('apps').closest('button') as HTMLButtonElement;
  // A real click on the chip moves focus to it, out of the text field.
  const openModelPanel = () => {
    const chip = screen.getAllByTestId('chat-model-selector')[0];
    act(() => chip.focus());
    fireEvent.click(chip);
    expect(screen.getByText('Pick Fast One')).toBeTruthy();
  };

  it('paints the same surface as the chat composer with the model panel open', () => {
    const chat = renderInput();
    openModelPanel();
    const chatSurface = surface(card(chat.container));
    cleanup();

    const dock = openDock();
    openModelPanel();

    expect(surface(card(dock.container))).toEqual(chatSurface);
    expect(card(dock.container).style.background).not.toContain('color-mix');
  });

  it('paints the same surface as the chat composer while you type', () => {
    const chat = renderInput();
    fireEvent.focus(composer());
    type('What changed?');
    const chatSurface = surface(card(chat.container));
    cleanup();

    const dock = openDock();
    fireEvent.focus(composer());
    type('What changed?');

    expect(surface(card(dock.container))).toEqual(chatSurface);
  });

  it('has the same toolbar as the chat composer, apps button included', () => {
    const chat = renderInput();
    const chatButtons = within(card(chat.container)).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent);
    cleanup();

    const dock = openDock();
    const dockButtons = within(card(dock.container)).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent);

    expect(dockButtons).toEqual(chatButtons);
    expect(appsButton()).toBeTruthy();
  });

  it('scopes the question to the sources picked in its apps panel, and leaves the chat page selection alone', () => {
    const { onSend } = openDock();

    fireEvent.click(appsButton());
    fireEvent.click(screen.getByText('Choose the handbook'));
    fireEvent.click(appsButton());
    type('What is in it?');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith(
      'What is in it?',
      undefined,
      [expect.objectContaining({ id: 'kb-handbook' })],
    );
    expect(useChatStore.getState().settings.filters).toEqual(initialChatState.settings.filters);
  });

  it('in agent mode too, the apps panel picks scope its own question, not the chat page', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, queryMode: 'agent' } });
    const { onSend } = openDock();

    fireEvent.click(appsButton());
    expect(screen.getByText('Universal agent resources')).toBeTruthy();
    fireEvent.click(screen.getByText('Choose the handbook'));
    fireEvent.click(appsButton());
    type('What is in it?');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith(
      'What is in it?',
      undefined,
      [expect.objectContaining({ id: 'kb-handbook' })],
    );
    expect(useChatStore.getState().settings.filters).toEqual(initialChatState.settings.filters);
  });

  it('stays open while the apps panel is in its larger overlay', () => {
    openDock();
    fireEvent.click(appsButton());
    fireEvent.click(screen.getByText('Toggle panel size'));
    expect(screen.getByRole('dialog', { name: 'Expanded panel' })).toBeTruthy();

    // The real overlay is portaled to the body with a scrim over the page, not inside the dock.
    fireEvent.mouseDown(document.body);

    expect(screen.getByRole('dialog', { name: 'Expanded panel' })).toBeTruthy();
    expect(screen.queryByPlaceholderText('Ask about this page')).toBeNull();
  });

  it('stays open for a click in a dialog opened from one of its panels', () => {
    openDock();
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    const field = document.createElement('input');
    dialog.appendChild(field);
    document.body.appendChild(dialog);

    fireEvent.mouseDown(field);

    expect(screen.queryByPlaceholderText('Ask about this page')).toBeNull();
    dialog.remove();
  });

  // The larger view's mode is shared chat state. Left set by a composer on another page (new chat, then Collections in
  // the sidebar), it must not keep this one open.
  it('folds on click-away even when another composer left the larger view set', () => {
    useChatStore.setState({ expansionViewMode: 'overlay' });
    openDock();

    fireEvent.mouseDown(document.body);

    expect(screen.getByPlaceholderText('Ask about this page')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Ask anything...')).toBeNull();
  });

  it('with the larger view left set elsewhere, Escape still closes its panel and click-away still folds it', () => {
    useChatStore.setState({ expansionViewMode: 'overlay' });
    openDock();
    openModelPanel();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('Pick Fast One')).toBeNull();

    fireEvent.mouseDown(document.body);
    expect(screen.getByPlaceholderText('Ask about this page')).toBeTruthy();
  });

  it('writes the picks made in the larger view to its own question too', () => {
    useChatStore.setState({ settings: { ...useChatStore.getState().settings, queryMode: 'agent' } });
    const { onSend } = openDock();

    fireEvent.click(appsButton());
    fireEvent.click(screen.getByText('Toggle panel size'));
    const overlay = screen.getByRole('dialog', { name: 'Expanded panel' });
    fireEvent.click(within(overlay).getByText('Choose the handbook'));
    fireEvent.click(within(overlay).getByText('Toggle panel size'));
    fireEvent.click(appsButton());
    type('What is in it?');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('What is in it?', undefined, [expect.objectContaining({ id: 'kb-handbook' })]);
    expect(useChatStore.getState().settings.filters).toEqual(initialChatState.settings.filters);
  });

  it('writes the picks made in the larger connectors view to its own question too', () => {
    const { onSend } = openDock();

    fireEvent.click(appsButton());
    fireEvent.click(screen.getByText('Toggle panel size'));
    const overlay = screen.getByRole('dialog', { name: 'Expanded panel' });
    fireEvent.click(within(overlay).getByText('Choose the handbook'));
    fireEvent.click(within(overlay).getByText('Toggle panel size'));
    fireEvent.click(appsButton());
    type('What is in it?');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('What is in it?', undefined, [expect.objectContaining({ id: 'kb-handbook' })]);
    expect(useChatStore.getState().settings.filters).toEqual(initialChatState.settings.filters);
  });

  it('still folds back to one line when you click away from it', () => {
    openDock();

    fireEvent.mouseDown(document.body);

    expect(screen.getByPlaceholderText('Ask about this page')).toBeTruthy();
    expect(screen.queryByPlaceholderText('Ask anything...')).toBeNull();
  });
});

describe('ChatInput — on a phone', () => {
  it('opens the query options sheet from the more button', () => {
    device.isMobile = true;
    renderInput();

    fireEvent.click(screen.getByText('more_horiz').closest('button') as HTMLButtonElement);

    expect(screen.getByText('Query options')).toBeTruthy();
  });

  it('in the Collections and All records dock, sources picked in the sheet scope its own question', () => {
    device.isMobile = true;
    const { onSend } = renderInput({ variant: 'widget', expandable: true, widgetPlaceholder: 'Ask about this page' });
    fireEvent.click(screen.getByRole('button', { name: 'Expand composer' }));

    fireEvent.click(screen.getByText('more_horiz').closest('button') as HTMLButtonElement);
    fireEvent.mouseDown(screen.getByText('Choose the handbook on the phone'));
    fireEvent.click(screen.getByText('Choose the handbook on the phone'));
    fireEvent.click(screen.getByText('Done'));
    type('What is in it?');
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('What is in it?', undefined, [expect.objectContaining({ id: 'kb-handbook' })]);
    expect(useChatStore.getState().settings.filters).toEqual(initialChatState.settings.filters);
  });
});

describe('ChatInput — keeping an unsent message', () => {
  const KEY_CONV_1 = 'pipeshub-chat-draft:u1:-:conv-1';
  const KEY_CONV_2 = 'pipeshub-chat-draft:u1:-:conv-2';
  const KEY_NEW = 'pipeshub-chat-draft:u1:-:new';

  function openConversation(convId: string) {
    const store = useChatStore.getState();
    const slotId = store.createSlot(convId);
    store.updateSlot(slotId, { isInitialized: true });
    store.setActiveSlot(slotId);
    return slotId;
  }

  const pause = () => act(() => void vi.advanceTimersByTime(DRAFT_SAVE_DEBOUNCE_MS + 50));

  beforeEach(() => {
    window.localStorage.clear();
    allowChatDraftWrites();
    useAuthStore.setState({ user: { id: 'u1' } });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.setState({ user: null, accessToken: null });
    window.localStorage.clear();
  });

  function fakeJwt(userId: string): string {
    const encode = (value: object) =>
      btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return `${encode({ alg: 'none' })}.${encode({ userId })}.sig`;
  }

  it('knows the user from the login token alone, as after a page reload', () => {
    useAuthStore.setState({ user: null, accessToken: fakeJwt('u1') });
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Written before the reload');

    renderInput({ persistDraft: true });

    expect(composer().value).toBe('Written before the reload');
  });

  it('saves a draft when only the login token identifies the user', () => {
    useAuthStore.setState({ user: null, accessToken: fakeJwt('u1') });
    openConversation('conv-1');
    renderInput({ persistDraft: true });

    type('Typed after a reload');
    pause();

    expect(loadDraft(KEY_CONV_1)).toBe('Typed after a reload');
  });

  it('keeps drafts when the login token is refreshed', () => {
    useAuthStore.setState({ user: null, accessToken: fakeJwt('u1') });
    openConversation('conv-1');
    renderInput({ persistDraft: true });
    type('Typed before a token refresh');

    act(() => useAuthStore.setState({ accessToken: fakeJwt('u1') + '' }));
    pause();

    expect(composer().value).toBe('Typed before a token refresh');
    expect(loadDraft(KEY_CONV_1)).toBe('Typed before a token refresh');
  });

  it('puts back what was typed in this conversation before the page was left', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Which vendors renew in Q4?');

    renderInput({ persistDraft: true });

    expect(composer().value).toBe('Which vendors renew in Q4?');
    expect(sendButton().disabled).toBe(false);
  });

  it('saves what is typed once the user pauses', () => {
    openConversation('conv-1');
    renderInput({ persistDraft: true });

    type('Which vendors renew in Q4?');
    expect(loadDraft(KEY_CONV_1)).toBe('');
    pause();

    expect(loadDraft(KEY_CONV_1)).toBe('Which vendors renew in Q4?');
  });

  it('keeps the message across a reload of the page', () => {
    openConversation('conv-1');
    const first = renderInput({ persistDraft: true });
    type('Which vendors renew in Q4?');
    first.unmount();

    renderInput({ persistDraft: true });

    expect(composer().value).toBe('Which vendors renew in Q4?');
  });

  it('keeps a draft in a new chat that has no conversation yet', () => {
    const first = renderInput({ persistDraft: true });
    type('A question for a new chat');
    first.unmount();

    renderInput({ persistDraft: true });

    expect(composer().value).toBe('A question for a new chat');
    expect(loadDraft(KEY_NEW)).toBe('A question for a new chat');
  });

  it('forgets the draft once the message is sent', () => {
    const { onSend } = renderInput({ persistDraft: true });
    type('Which vendors renew in Q4?');
    pause();
    expect(loadDraft(KEY_NEW)).not.toBe('');

    fireEvent.click(sendButton());
    pause();

    expect(onSend).toHaveBeenCalledWith('Which vendors renew in Q4?', undefined, undefined);
    expect(composer().value).toBe('');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('forgets the draft when the page is left right after sending', () => {
    const { unmount } = renderInput({ persistDraft: true });
    type('Which vendors renew in Q4?');
    pause();

    fireEvent.click(sendButton());
    unmount();

    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('keeps a separate draft for each conversation', () => {
    const first = openConversation('conv-1');
    saveDraft(KEY_CONV_2, 'Draft waiting in the second chat');
    renderInput({ persistDraft: true });
    type('Typed in the first chat');

    const second = openConversation('conv-2');
    act(() => useChatStore.getState().setActiveSlot(second));
    expect(composer().value).toBe('Draft waiting in the second chat');

    act(() => useChatStore.getState().setActiveSlot(first));
    expect(composer().value).toBe('Typed in the first chat');
  });

  it('does not carry text into another conversation that has no draft', () => {
    const first = openConversation('conv-1');
    renderInput({ persistDraft: true });
    type('Typed in the first chat');

    const second = openConversation('conv-2');
    act(() => useChatStore.getState().setActiveSlot(second));

    expect(composer().value).toBe('');
    pause();
    expect(window.localStorage.getItem(KEY_CONV_2)).toBeNull();
    expect(useChatStore.getState().activeSlotId).not.toBe(first);
  });

  it('does not bring a sent message back when a new chat becomes a conversation', () => {
    renderInput({ persistDraft: true });
    type('My first question');
    pause();

    fireEvent.click(sendButton());
    act(() => {
      const store = useChatStore.getState();
      const slotId = store.createSlot(null);
      store.setActiveSlot(slotId);
      store.resolveSlotConvId(slotId, 'conv-9');
    });
    pause();

    expect(composer().value).toBe('');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('keeps text typed while a new chat is still answering', () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot(null);
    store.setActiveSlot(slotId);
    renderInput({ persistDraft: true });
    type('A follow-up typed during the answer');

    act(() => useChatStore.getState().resolveSlotConvId(slotId, 'conv-9'));
    pause();

    expect(composer().value).toBe('A follow-up typed during the answer');
    expect(loadDraft('pipeshub-chat-draft:u1:-:conv-9')).toBe('A follow-up typed during the answer');
  });

  it('does not store the question being edited as a draft', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'My own draft');
    renderInput({ persistDraft: true });

    act(() => {
      useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original question' });
    });
    pause();

    expect(composer().value).toBe('Original question');
    expect(loadDraft(KEY_CONV_1)).toBe('My own draft');
  });

  it('does not touch the draft of a conversation when the reader leaves while regenerating', () => {
    const first = openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'My own draft');
    renderInput({ persistDraft: true });
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-7', text: 'Old question' });
    });
    const second = openConversation('conv-2');

    act(() => useChatStore.getState().setActiveSlot(second));
    pause();

    expect(loadDraft(KEY_CONV_1)).toBe('My own draft');
    expect(useChatStore.getState().activeSlotId).not.toBe(first);
  });

  it('leaves no draft behind when the user signs out while text is still in the box', () => {
    openConversation('conv-1');
    const { unmount } = renderInput({ persistDraft: true });
    type('Confidential, not sent yet');
    pause();
    expect(loadDraft(KEY_CONV_1)).toBe('Confidential, not sent yet');

    discardAllChatDrafts();
    type('Confidential, typed during sign-out');
    window.dispatchEvent(new Event('pagehide'));
    unmount();
    pause();

    expect(window.localStorage.getItem(KEY_CONV_1)).toBeNull();
  });

  it('starts keeping drafts again after the next sign-in', () => {
    openConversation('conv-1');
    discardAllChatDrafts();
    allowChatDraftWrites();
    renderInput({ persistDraft: true });

    type('Back again');
    pause();

    expect(loadDraft(KEY_CONV_1)).toBe('Back again');
  });

  it('keeps an agent chat draft apart from the same conversation id outside the agent', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Plain chat draft');
    saveDraft('pipeshub-chat-draft:u1:agent-7:conv-1', 'Agent chat draft');

    const { unmount } = renderInput({ persistDraft: true, agentId: 'agent-7' });
    expect(composer().value).toBe('Agent chat draft');
    unmount();

    renderInput({ persistDraft: true });
    expect(composer().value).toBe('Plain chat draft');
  });

  it('keeps each agent’s new-chat draft apart', () => {
    saveDraft('pipeshub-chat-draft:u1:agent-7:new', 'For agent seven');

    const { unmount } = renderInput({ persistDraft: true, agentId: 'agent-8' });
    expect(composer().value).toBe('');
    unmount();

    renderInput({ persistDraft: true, agentId: 'agent-7' });
    expect(composer().value).toBe('For agent seven');
  });

  it('does not hand text left in the box to the next user when another tab signs in as someone else', () => {
    openConversation('conv-1');
    renderInput({ persistDraft: true });
    type('Private to the first user');
    pause();

    act(() => useAuthStore.setState({ user: { id: 'u2' } }));
    pause();

    expect(composer().value).toBe('');
    expect(window.localStorage.getItem('pipeshub-chat-draft:u2:-:conv-1')).toBeNull();
    expect(loadDraft(KEY_CONV_1)).toBe('Private to the first user');
  });

  it('does not keep anything for a signed-out visitor', () => {
    useAuthStore.setState({ user: null });
    renderInput({ persistDraft: true });

    type('Should not be stored');
    pause();

    expect(window.localStorage.length).toBe(0);
  });

  it('keeps one person’s draft away from another person on the same browser', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Private to the first person');
    useAuthStore.setState({ user: { id: 'u2' } });

    renderInput({ persistDraft: true });

    expect(composer().value).toBe('');
  });

  it('leaves the composer alone where drafts are not asked for', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Should not appear');
    const { unmount } = renderInput();

    expect(composer().value).toBe('');
    type('Typed in the widget');
    pause();
    unmount();

    expect(loadDraft(KEY_CONV_1)).toBe('Should not appear');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('still lets the user type and send when the browser blocks storage', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const { onSend } = renderInput({ persistDraft: true });

    type('Still works');
    pause();
    fireEvent.click(sendButton());

    expect(onSend).toHaveBeenCalledWith('Still works', undefined, undefined);
    vi.restoreAllMocks();
  });

  it('does not overwrite a suggestion chosen as the box opens', () => {
    openConversation('conv-1');
    saveDraft(KEY_CONV_1, 'Old draft');

    renderInput({ persistDraft: true, prefill: { text: 'Summarise the handbook', key: 1 } });

    expect(composer().value).toBe('Summarise the handbook');
  });
});

describe('ChatInput — a sent message is not kept as a draft', () => {
  const KEY_NEW = 'pipeshub-chat-draft:u1:-:new';
  const pause = () => act(() => void vi.advanceTimersByTime(DRAFT_SAVE_DEBOUNCE_MS + 50));
  const settleHost = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // The first message of a new chat moves the composer to another place in the page,
  // so the old composer is unmounted and a new one is mounted as the message is sent.
  function MovingHost({ answer }: { answer: 'now' | 'later' | 'refused-later' }) {
    const [moved, setMoved] = React.useState(false);
    const onSend = () => {
      setMoved(true);
      if (answer === 'later') return Promise.resolve(true);
      if (answer === 'refused-later') return Promise.resolve(false);
      return undefined;
    };
    return (
      <Theme>
        {moved ? (
          <section data-testid="message-list-layout">
            <ChatInput key="after" onSend={onSend} persistDraft />
          </section>
        ) : (
          <ChatInput key="before" onSend={onSend} persistDraft />
        )}
      </Theme>
    );
  }

  function StayingHost({ answer }: { answer: 'now' | 'refused' | 'rejected' | 'later' }) {
    const onSend = () => {
      if (answer === 'refused') return false;
      if (answer === 'rejected') return Promise.reject(new Error('network'));
      if (answer === 'later') return Promise.resolve(true);
      return undefined;
    };
    return (
      <Theme>
        <ChatInput onSend={onSend} persistDraft />
      </Theme>
    );
  }

  beforeEach(() => {
    window.localStorage.clear();
    allowChatDraftWrites();
    useAuthStore.setState({ user: { id: 'u1' } });
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.setState({ user: null, accessToken: null });
    window.localStorage.clear();
  });

  it('does not bring the first message back when the composer moves as it is sent', async () => {
    render(<MovingHost answer="now" />);
    type('hiii, what are u doing');
    pause();

    fireEvent.click(sendButton());
    await settleHost();
    pause();

    expect(composer().value).toBe('');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('does not bring it back either when the host only answers after the composer has moved', async () => {
    render(<MovingHost answer="later" />);
    type('hiii, what are u doing');
    pause();

    fireEvent.click(sendButton());
    await settleHost();
    pause();

    expect(composer().value).toBe('');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('does not bring it back when the message is sent before the draft was ever saved', async () => {
    render(<MovingHost answer="later" />);
    type('sent before the pause ended');

    fireEvent.click(sendButton());
    await settleHost();
    pause();

    expect(composer().value).toBe('');
    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('removes the stored draft the moment the message is sent', () => {
    render(<StayingHost answer="later" />);
    type('about to be sent');
    pause();
    expect(loadDraft(KEY_NEW)).toBe('about to be sent');

    fireEvent.click(sendButton());

    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
  });

  it('does not save the sent text while the host is still answering', async () => {
    render(<StayingHost answer="later" />);
    type('about to be sent');
    fireEvent.click(sendButton());

    pause();
    window.dispatchEvent(new Event('pagehide'));

    expect(window.localStorage.getItem(KEY_NEW)).toBeNull();
    await settleHost();
  });

  it('keeps the text, and saves it again, when the host refuses the message', async () => {
    render(<StayingHost answer="refused" />);
    type('refused message');

    fireEvent.click(sendButton());
    pause();

    expect(composer().value).toBe('refused message');
    expect(loadDraft(KEY_NEW)).toBe('refused message');
  });

  it('keeps the text, and saves it again, when sending fails', async () => {
    render(<StayingHost answer="rejected" />);
    type('failed message');

    fireEvent.click(sendButton());
    await settleHost();
    pause();

    expect(composer().value).toBe('failed message');
    expect(loadDraft(KEY_NEW)).toBe('failed message');
  });

  it('brings a refused message back after a reload', async () => {
    const first = render(<StayingHost answer="refused" />);
    type('refused message');
    fireEvent.click(sendButton());
    first.unmount();

    render(<StayingHost answer="refused" />);

    expect(composer().value).toBe('refused message');
  });

  it('saves what is typed after a send again', async () => {
    render(<StayingHost answer="now" />);
    type('first message');
    fireEvent.click(sendButton());
    pause();

    type('the next question');
    pause();

    expect(loadDraft(KEY_NEW)).toBe('the next question');
  });

  it('saves what is typed while the host is still answering', async () => {
    render(<StayingHost answer="later" />);
    type('first message');
    fireEvent.click(sendButton());
    type('the next question, typed during the answer');

    pause();
    await settleHost();

    expect(loadDraft(KEY_NEW)).toBe('the next question, typed during the answer');
  });
});

describe('ChatInput — composer behaviour that must survive a change of input component', () => {
  beforeEach(() => window.localStorage.clear());

  it('consumes Enter so the browser inserts no newline, but leaves Shift+Enter to the browser', () => {
    renderInput();
    type('Line one');

    expect(fireEvent.keyDown(composer(), { key: 'Enter', shiftKey: true })).toBe(true);
    expect(fireEvent.keyDown(composer(), { key: 'Enter' })).toBe(false);
  });

  it('does not send on Enter when nothing was typed, and keeps the box open for typing', () => {
    const { onSend } = renderInput();

    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).not.toHaveBeenCalled();
    expect(composer().value).toBe('');
  });

  it('keeps multi-line text exactly as typed', () => {
    const { onSend } = renderInput();
    type('first\nsecond\n\nthird');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('first\nsecond\n\nthird', undefined, undefined);
  });

  it('leaves a short plain-text paste to the browser', () => {
    const onUploadFile = vi.fn();
    renderInput({ onUploadFile });

    const notCancelled = fireEvent.paste(composer(), {
      clipboardData: { items: [], getData: () => 'a short note' },
    });

    expect(notCancelled).toBe(true);
    expect(onUploadFile).not.toHaveBeenCalled();
    expect(screen.queryByText('Pasted text')).toBeNull();
  });

  it('takes over a pasted file and a very long paste, so the browser inserts nothing', () => {
    const onUploadFile = vi.fn(() => new Promise<AttachmentRef>(() => {}));
    renderInput({ onUploadFile });
    const shot = new File(['png'], 'diagram.png', { type: 'image/png' });
    const longText = Array.from({ length: 200 }, (_, i) => `Line ${i} of the pasted log`).join('\n');

    expect(
      fireEvent.paste(composer(), {
        clipboardData: { items: [{ kind: 'file', getAsFile: () => shot }], getData: () => '' },
      }),
    ).toBe(false);
    expect(
      fireEvent.paste(composer(), { clipboardData: { items: [], getData: () => longText } }),
    ).toBe(false);

    expect(onUploadFile).toHaveBeenCalledTimes(2);
    expect(composer().value).toBe('');
  });

  it('moves a pasted-text attachment back into the box and focuses it', async () => {
    // jsdom's Blob has no text()
    vi.stubGlobal('File', class extends File {
      text() {
        return Promise.resolve('Line 0\nLine 1');
      }
    });
    const onUploadFile = vi.fn().mockResolvedValue(ref('r-paste'));
    renderInput({ onUploadFile });
    type('Intro');
    const longText = Array.from({ length: 200 }, (_, i) => `Line ${i}`).join('\n');
    fireEvent.paste(composer(), { clipboardData: { items: [], getData: () => longText } });

    await act(async () => {});
    // The chip opens the reader; "Show in text field" lives there.
    fireEvent.click(screen.getByRole('button', { name: /^Pasted text,/ }));
    await act(async () => {});
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    try {
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Show in text field' }));
        for (let i = 0; i < 5; i++) await Promise.resolve();
        vi.runAllTimers();
      });
    } finally {
      vi.useRealTimers();
    }

    expect(composer().value.startsWith('Intro\n\nLine 0')).toBe(true);
    expect(screen.queryByText('Pasted text')).toBeNull();
    expect(document.activeElement).toBe(composer());
  });

  it('grows with the text up to 160px and goes back to one line once sent', () => {
    const { onSend } = renderInput();
    const box = composer();
    expect(box.style.maxHeight).toBe('160px');

    Object.defineProperty(box, 'scrollHeight', { configurable: true, value: 80 });
    type('a\nb\nc');
    fireEvent.input(box);
    expect(box.style.height).toBe('80px');

    Object.defineProperty(box, 'scrollHeight', { configurable: true, value: 400 });
    fireEvent.input(box);
    expect(box.style.height).toBe('160px');

    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalled();
    expect(box.style.height).toBe('auto');
  });

  it('puts the dictated words after the typed text while they are still being recognised', () => {
    speech.state = { ...speech.state, isListening: true, interimTranscript: 'what is' };
    renderInput();
    expect((screen.getByPlaceholderText('Listening...') as HTMLTextAreaElement).value).toBe('what is');

    cleanup();
    speech.state = { ...speech.state, isListening: false, interimTranscript: 'the plan' };
    renderInput();
    type('Tell me');
    expect(composer().value).toBe('Tell me the plan');
  });

  it('shows the question being regenerated, dimmed and not editable, and Escape empties it', () => {
    renderInput();
    act(() => {
      useCommandStore.getState().dispatch('showRegenBar', { messageId: 'msg-2', text: 'Earlier question' });
    });

    expect(composer().value).toBe('Earlier question');
    expect(composer().readOnly).toBe(true);
    // The field's text is drawn by the layer behind it, which carries the dimmed colour.
    expect((composer().previousElementSibling as HTMLElement).style.color).toBe('var(--text-placeholder)');

    fireEvent.keyDown(composer(), { key: 'Escape' });
    expect(composer().value).toBe('');
    expect(composer().readOnly).toBe(false);
    expect(composer().style.color).toBe('var(--text-primary)');
  });

  it('focuses the box when editing an earlier question and when a suggestion seeds it', () => {
    vi.useFakeTimers({ toFake: ['setTimeout'] });
    try {
      renderInput();
      act(() => {
        useCommandStore.getState().dispatch('showEditQuery', { messageId: 'msg-3', text: 'Original' });
      });
      act(() => {
        vi.runAllTimers();
      });
      expect(document.activeElement).toBe(composer());

      act(() => (document.activeElement as HTMLElement).blur());
      cleanup();
      const { rerender } = renderInput();
      rerender(
        <Theme>
          <ChatInput onSend={vi.fn()} prefill={{ key: 1, text: 'Seed' }} />
        </Theme>,
      );
      act(() => {
        vi.runAllTimers();
      });
      expect(composer().value).toBe('Seed');
      expect(document.activeElement).toBe(composer());
    } finally {
      vi.useRealTimers();
    }
  });

  it('clears the attachment chips and the text together on send', async () => {
    const upload = vi.fn().mockResolvedValue(ref('r1'));
    const { onSend } = renderInput({ onUploadFile: upload });
    pick(pdf());
    await act(async () => {});
    type('See attached');
    fireEvent.keyDown(composer(), { key: 'Enter' });

    expect(onSend).toHaveBeenCalledWith('See attached', [ref('r1')], undefined);
    expect(composer().value).toBe('');
    expect(screen.queryByText('report.pdf')).toBeNull();
  });
});
