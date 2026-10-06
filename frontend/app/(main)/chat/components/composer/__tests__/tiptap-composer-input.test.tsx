import React, { createRef } from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, act, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { Editor } from '@tiptap/react';
import '@/lib/__tests__/test-i18n';

const api = vi.hoisted(() => ({ getCollaborators: vi.fn() }));
vi.mock('@/chat/collaboration-api', () => ({ CollaborationApi: { getCollaborators: api.getCollaborators } }));

import { TiptapComposerInput, type TiptapComposerInputProps } from '../tiptap-composer-input';
import { rememberMentionLabels } from '../use-mentionables';
import type { ComposerInputHandle } from '../composer-input.types';
import { useChatStore } from '@/chat/store';
import { useUserStore } from '@/lib/store/user-store';
import { useParticipantsStore } from '@/chat/mentions/participants-store';

const initialChatState = useChatStore.getState();

beforeEach(() => {
  class FakeResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
  const rect = { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, toJSON: () => ({}) };
  Range.prototype.getBoundingClientRect = () => rect as DOMRect;
  Range.prototype.getClientRects = () =>
    ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
  document.elementFromPoint = () => null;

  useChatStore.setState(initialChatState, true);
  const slotId = useChatStore.getState().createSlot(null);
  useChatStore.getState().setActiveSlot(slotId);
  useChatStore.getState().updateSlot(slotId, { convId: 'conv-1' });
  useUserStore.setState({ profile: { userId: 'me' } as never });
  api.getCollaborators.mockReset();
  useParticipantsStore.getState().reset();
  api.getCollaborators.mockResolvedValue({
    owner: { userId: 'owner-1', displayName: 'Olive Owner' },
    collaborators: [
      { principalType: 'user', principalId: 'u-bob', displayName: 'Bob Builder', accessLevel: 'write', state: 'active' },
      { principalType: 'user', principalId: 'u-gone', displayName: 'Gone Gary', accessLevel: 'read', state: 'former_member' },
      { principalType: 'team', principalId: 't-sales', displayName: 'Sales', accessLevel: 'read', state: 'active' },
    ],
    collaboratorCount: 3,
    settings: { editorsCanInvite: false, ownerContentShared: false },
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type HarnessProps = Partial<TiptapComposerInputProps> & {
  handle?: React.Ref<ComposerInputHandle>;
  initial?: string;
  spy?: { onValue?: (v: string) => void };
};

function Harness({ handle, initial = '', spy, ...rest }: HarnessProps) {
  const [value, setValue] = React.useState(initial);
  return (
    <Theme>
      <div data-testid="container" onPaste={() => spy && undefined}>
        <TiptapComposerInput
          ref={handle}
          value={value}
          placeholder="Ask anything..."
          ariaLabel="Message"
          onChange={(t) => {
            setValue(t);
            spy?.onValue?.(t);
          }}
          onKeyDown={() => {}}
          {...rest}
        />
      </div>
      <output data-testid="value">{value}</output>
    </Theme>
  );
}

const box = () => screen.getByTestId('chat-composer');
const editorOf = () => (box() as unknown as { editor: Editor }).editor;
const valueOut = () => screen.getByTestId('value').textContent;

async function mount(props: HarnessProps = {}) {
  const ref = createRef<ComposerInputHandle>();
  const utils = render(<Harness handle={ref} {...props} />);
  await waitFor(() => expect(screen.getByTestId('chat-composer')).toBeTruthy());
  return { ref, ...utils };
}

async function typeAt(text = '@') {
  await act(async () => {
    editorOf().chain().focus('end').insertContent(text).run();
  });
}

describe('TiptapComposerInput: the port', () => {
  it('is a multiline, labelled textbox with the shared test id', async () => {
    await mount();
    expect(box().getAttribute('role')).toBe('textbox');
    expect(box().getAttribute('aria-multiline')).toBe('true');
    expect(box().getAttribute('aria-label')).toBe('Message');
    expect(box().getAttribute('contenteditable')).toBe('true');
  });

  it('shows the placeholder while empty and follows prop changes', async () => {
    const { rerender } = await mount();
    expect(box().querySelector('p')?.getAttribute('data-placeholder')).toBe('Ask anything...');
    rerender(<Harness placeholder="Listening..." />);
    await waitFor(() => expect(box().querySelector('p')?.getAttribute('data-placeholder')).toBe('Listening...'));
  });

  it('reports typed text through onChange and keeps the value controlled', async () => {
    const onValue = vi.fn();
    await mount({ spy: { onValue } });
    await typeAt('hello');
    expect(onValue).toHaveBeenLastCalledWith('hello');
    expect(valueOut()).toBe('hello');
  });

  it('PH10-03: an external value change (voice transcript) replaces the content; interim is shown but never committed', async () => {
    const onValue = vi.fn();
    const { ref, rerender } = await mount({ spy: { onValue } });
    await act(async () => {
      ref.current?.setText('hello');
    });
    rerender(<Harness handle={ref} spy={{ onValue }} initial="hello" interim="world" />);
    await waitFor(() => expect(box().querySelector('.ph-composer-interim')?.textContent).toBe(' world'));
    expect(ref.current?.getValue().text).toBe('hello');
    expect(onValue).not.toHaveBeenCalledWith(expect.stringContaining('world'));
  });

  it('clear() empties the editor and reports an empty value', async () => {
    const { ref } = await mount({ initial: 'draft' });
    await waitFor(() => expect(box().textContent).toContain('draft'));
    await act(async () => {
      ref.current?.clear();
    });
    expect(box().textContent).toBe('');
    expect(valueOut()).toBe('');
  });

  it('PH10-02: readOnly makes the editor non-editable but keeps the text', async () => {
    await mount({ initial: 'regenerate me', readOnly: true });
    await waitFor(() => expect(box().getAttribute('contenteditable')).toBe('false'));
    expect(box().textContent).toContain('regenerate me');
    expect(box().getAttribute('aria-readonly')).toBe('true');
  });

  it('PH10-05: the editor scrolls inside a 120px cap', async () => {
    await mount({ initial: Array.from({ length: 20 }, (_, i) => `line ${i}`).join('\n') });
    const wrapper = box().closest('.ph-composer-editor') as HTMLElement;
    expect(wrapper.style.maxHeight).toBe('120px');
    expect(wrapper.style.overflowY).toBe('auto');
  });

  it('insertMention adds a chip and a trailing space, and getValue lists it', async () => {
    const { ref } = await mount();
    await act(async () => {
      ref.current?.insertMention({ type: 'user', id: 'u-bob' }, 'Bob Builder');
    });
    expect(valueOut()).toBe('<@user:u-bob> ');
    expect(ref.current?.getValue()).toEqual({ text: '<@user:u-bob> ', mentions: [{ type: 'user', id: 'u-bob' }] });
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Bob Builder');
  });
});

describe('TiptapComposerInput: keys', () => {
  it('PH10-06: Enter reaches the container and adds no line; Shift+Enter inserts a line break', async () => {
    const onKeyDown = vi.fn();
    await mount({ initial: 'ab', onKeyDown });
    await waitFor(() => expect(box().textContent).toContain('ab'));
    act(() => {
      editorOf().commands.focus('end');
    });
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onKeyDown).toHaveBeenCalledTimes(1);
    expect(valueOut()).toBe('ab');

    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    await waitFor(() => expect(valueOut()).toBe('ab\n'));
  });

  it('PH10-06: an Enter pressed during IME composition is flagged so the container does not send', async () => {
    const onKeyDown = vi.fn();
    await mount({ initial: 'ni', onKeyDown });
    await waitFor(() => expect(box().textContent).toContain('ni'));
    fireEvent.keyDown(box(), { key: 'Enter', keyCode: 229, isComposing: true });
    expect(onKeyDown).toHaveBeenCalledTimes(1);
    expect((onKeyDown.mock.calls[0][0] as React.KeyboardEvent).nativeEvent.isComposing).toBe(true);
    expect(valueOut()).toBe('ni');
  });

  it('MN-16 neighbour: Backspace right after a chip removes the whole chip', async () => {
    const { ref } = await mount({ initial: 'x ' });
    await waitFor(() => expect(box().textContent).toContain('x'));
    await act(async () => {
      ref.current?.focus();
      ref.current?.insertMention({ type: 'user', id: 'u-bob' }, 'Bob Builder');
    });
    act(() => {
      const e = editorOf();
      let after = 0;
      e.state.doc.descendants((node, pos) => {
        if (node.type.name === 'mention') after = pos + node.nodeSize;
      });
      e.commands.setTextSelection(after);
    });
    fireEvent.keyDown(box(), { key: 'Backspace' });
    await waitFor(() => expect(screen.queryByTestId('mention-chip')).toBeNull());
    expect(valueOut()).toBe('x  ');
    expect(ref.current?.getValue().mentions).toEqual([]);
  });
});

function pasteData(parts: { text?: string; files?: File[] }) {
  const files = parts.files ?? [];
  return {
    getData: (type: string) => (type === 'text/plain' ? (parts.text ?? '') : ''),
    items: files.map((f) => ({ kind: 'file', type: f.type, getAsFile: () => f })),
    files,
    types: [...(parts.text ? ['text/plain'] : []), ...(files.length ? ['Files'] : [])],
  };
}

describe('TiptapComposerInput: paste', () => {
  it('PH10-04: pasted text goes in as plain text, with line breaks as breaks', async () => {
    await mount();
    act(() => {
      editorOf().commands.focus();
    });
    fireEvent.paste(box(), { clipboardData: pasteData({ text: 'one\r\ntwo' }) });
    await waitFor(() => expect(valueOut()).toBe('one\ntwo'));
  });

  it('PH10-04: a paste the container will turn into a chip inserts nothing and still bubbles to the container', async () => {
    const containerPaste = vi.fn();
    const shouldDeferPaste = vi.fn().mockReturnValue(true);
    render(
      <Theme>
        <div onPaste={containerPaste}>
          <TiptapComposerInput
            value=""
            placeholder="p"
            ariaLabel="Message"
            onChange={() => {}}
            onKeyDown={() => {}}
            shouldDeferPaste={shouldDeferPaste}
          />
        </div>
      </Theme>,
    );
    await waitFor(() => expect(screen.getByTestId('chat-composer')).toBeTruthy());
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });
    fireEvent.paste(box(), { clipboardData: pasteData({ text: 'a.pdf', files: [file] }) });
    expect(shouldDeferPaste).toHaveBeenCalled();
    expect(containerPaste).toHaveBeenCalledTimes(1);
    expect(box().textContent).toBe('');

    fireEvent.paste(box(), { clipboardData: pasteData({ text: 'x'.repeat(5000) }) });
    expect(containerPaste).toHaveBeenCalledTimes(2);
    expect(box().textContent).toBe('');
  });

  it('MN-04: pasted or typed <@...> text is stored escaped and carries no mention', async () => {
    const { ref } = await mount();
    act(() => {
      editorOf().commands.focus();
    });
    fireEvent.paste(box(), { clipboardData: pasteData({ text: 'see <@agent:xyz>' }) });
    await waitFor(() => expect(valueOut()).toBe('see <\\@agent:xyz>'));
    expect(ref.current?.getValue().mentions).toEqual([]);
  });
});

describe('TiptapComposerInput: @ popover', () => {
  const options = () => within(screen.getByRole('listbox')).getAllByRole('option');
  const selected = () => options().findIndex((o) => o.getAttribute('aria-selected') === 'true');

  it('opens on @, lists the assistant, people and teams, never the user themself or a former member', async () => {
    await mount();
    await typeAt('@');
    await waitFor(() => expect(screen.getByRole('listbox')).toBeTruthy());
    await waitFor(() => expect(options().length).toBe(4));
    expect(options().map((o) => o.textContent)).toEqual(['Assistant', 'Olive Owner', 'Bob Builder', 'Sales · team']);
  });

  it('filters by what is typed after the @ (debounced)', async () => {
    await mount();
    await typeAt('@bob');
    await waitFor(() => expect(options().map((o) => o.textContent)).toEqual(['Bob Builder']));
  });

  it('MN-18: arrows move the selection (wrapping), and the active option is exposed on the textbox and the listbox', async () => {
    await mount();
    await typeAt('@');
    await waitFor(() => expect(options().length).toBe(4));
    expect(selected()).toBe(0);
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    await waitFor(() => expect(selected()).toBe(1));
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    fireEvent.keyDown(box(), { key: 'ArrowUp' });
    await waitFor(() => expect(selected()).toBe(3));
    const active = options()[3].id;
    expect(screen.getByRole('listbox').getAttribute('aria-activedescendant')).toBe(active);
    expect(box().getAttribute('aria-activedescendant')).toBe(active);
    expect(box().getAttribute('aria-controls')).toBe(screen.getByRole('listbox').id);
    expect(box().getAttribute('aria-autocomplete')).toBe('list');
  });

  it('MN-18: Enter picks the active option as a chip and is not forwarded as a send', async () => {
    const onKeyDown = vi.fn();
    const { ref } = await mount({ onKeyDown });
    await typeAt('hi @bob');
    await waitFor(() => expect(options().length).toBe(1));
    fireEvent.keyDown(box(), { key: 'Enter' });
    await waitFor(() => expect(valueOut()).toBe('hi <@user:u-bob> '));
    expect(onKeyDown).not.toHaveBeenCalled();
    expect(ref.current?.getValue().mentions).toEqual([{ type: 'user', id: 'u-bob' }]);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('MN-18: Tab picks too', async () => {
    await mount();
    await typeAt('@');
    await waitFor(() => expect(options().length).toBe(4));
    fireEvent.keyDown(box(), { key: 'ArrowDown' });
    await waitFor(() => expect(selected()).toBe(1));
    fireEvent.keyDown(box(), { key: 'Tab' });
    await waitFor(() => expect(valueOut()).toBe('<@user:owner-1> '));
  });

  it('MN-18: Escape closes without inserting, and the next Enter is the container’s again', async () => {
    const onKeyDown = vi.fn();
    await mount({ onKeyDown });
    await typeAt('@');
    await waitFor(() => expect(options().length).toBe(4));
    fireEvent.keyDown(box(), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
    expect(onKeyDown).not.toHaveBeenCalled();
    expect(valueOut()).toBe('@');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onKeyDown).toHaveBeenCalledTimes(1);
  });

  it('a click on an option picks it', async () => {
    await mount();
    await typeAt('@');
    await waitFor(() => expect(options().length).toBe(4));
    fireEvent.click(options()[3]);
    await waitFor(() => expect(valueOut()).toBe('<@team:t-sales> '));
  });

  it('a non-manager gets the owner and feed authors only (summary view)', async () => {
    api.getCollaborators.mockResolvedValue({ owner: { userId: 'owner-1', displayName: 'Olive Owner' }, collaboratorCount: 5, myAccess: 'write' });
    const slotId = useChatStore.getState().activeSlotId!;
    useChatStore.getState().updateSlot(slotId, {
      messages: [
        { role: 'user', content: [{ type: 'text', text: 'q' }], metadata: { custom: { author: { userId: 'u-cara', displayName: 'Cara' } } } },
      ] as never,
    });
    await mount();
    await typeAt('@');
    await waitFor(() => expect(options().map((o) => o.textContent)).toEqual(['Assistant', 'Olive Owner', 'Cara']));
  });

  it('still offers the assistant when the participant lookup fails', async () => {
    api.getCollaborators.mockRejectedValue(new Error('boom'));
    await mount();
    await typeAt('@');
    await waitFor(() => expect(options().map((o) => o.textContent)).toEqual(['Assistant']));
  });

  it('MN-17: the chat’s own agent named "Assistant" is a separate "Assistant · agent" row that inserts an agent token', async () => {
    const slotId = useChatStore.getState().activeSlotId!;
    useChatStore.getState().updateSlot(slotId, { threadAgentId: 'agent-key-1' });
    useChatStore.setState({ agentContextDisplayName: 'Assistant' });
    await mount();
    await typeAt('@ass');
    await waitFor(() => expect(options().map((o) => o.textContent)).toEqual(['Assistant', 'Assistant · agent']));
    fireEvent.click(options()[1]);
    await waitFor(() => expect(valueOut()).toBe('<@agent:agent-key-1> '));
  });
});

describe('TiptapComposerInput: prefill', () => {
  it('MN-16: a chip with no known name renders "Unknown user" without throwing', async () => {
    api.getCollaborators.mockResolvedValue({ owner: { userId: 'owner-1', displayName: 'Olive Owner' }, collaboratorCount: 0, myAccess: 'read' });
    await mount({ initial: 'ask <@user:deleted-1>' });
    await waitFor(() => expect(screen.getByTestId('mention-chip').textContent).toBe('@Unknown user'));
    expect(valueOut()).toBe('ask <@user:deleted-1>');
  });

  it('fills chip names from the participants for an edit prefill', async () => {
    await mount({ initial: 'ask <@user:u-bob>' });
    await waitFor(() => expect(screen.getByTestId('mention-chip').textContent).toBe('@Bob Builder'));
  });

  it('uses names seen earlier in the session without another lookup', async () => {
    rememberMentionLabels([{ ref: { type: 'team', id: 't-x' }, label: 'Platform' }]);
    await mount({ initial: '<@team:t-x> hi' });
    await waitFor(() => expect(screen.getByTestId('mention-chip').textContent).toBe('@Platform'));
  });

  it('an escaped literal in a prefill is text, not a chip', async () => {
    await mount({ initial: 'a <\\@user:u-bob> b' });
    await waitFor(() => expect(box().textContent).toContain('a <@user:u-bob> b'));
    expect(screen.queryByTestId('mention-chip')).toBeNull();
    expect(valueOut()).toBe('a <\\@user:u-bob> b');
  });
});
