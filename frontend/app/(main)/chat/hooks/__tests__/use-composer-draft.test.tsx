import React, { StrictMode, useEffect, useState } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react';
import { DRAFT_SAVE_DEBOUNCE_MS, useComposerDraft } from '../use-composer-draft';
import { DRAFT_MAX_CHARS, loadDraft, saveDraft } from '../../utils/composer-draft';

const KEY_A = 'pipeshub-chat-draft:u1:-:conv-a';
const KEY_B = 'pipeshub-chat-draft:u1:-:conv-b';
const KEY_NEW = 'pipeshub-chat-draft:u1:-:new';

interface HarnessProps {
  draftKey: string | null;
  slotId: string | null;
  userId?: string | null;
  paused?: boolean;
  initial?: string;
}

let controls: ReturnType<typeof useComposerDraft> | null = null;

function Harness({ draftKey, slotId, paused, initial = '', userId = 'u1' }: HarnessProps) {
  const [message, setMessage] = useState(initial);
  const result = useComposerDraft({ draftKey, userId, slotId, message, setMessage, paused });
  useEffect(() => {
    controls = result;
  });
  return (
    <textarea
      aria-label="Message"
      value={message}
      onChange={(e) => setMessage(e.target.value)}
    />
  );
}

const box = () => screen.getByLabelText('Message') as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const wait = (ms: number) => act(() => void vi.advanceTimersByTime(ms));
const stored = (key: string) => window.localStorage.getItem(key);

beforeEach(() => {
  window.localStorage.clear();
  controls = null;
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('useComposerDraft — coming back', () => {
  it('puts back the text that was left in this conversation', () => {
    saveDraft(KEY_A, 'half a question');

    render(<Harness draftKey={KEY_A} slotId="s1" />);

    expect(box().value).toBe('half a question');
  });

  it('starts empty when nothing was left', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);

    expect(box().value).toBe('');
  });

  it('does not overwrite text that is already in the box', () => {
    saveDraft(KEY_A, 'old draft');

    render(<Harness draftKey={KEY_A} slotId="s1" initial="typed already" />);

    expect(box().value).toBe('typed already');
  });

  it('still restores the draft when React remounts the composer in development', () => {
    saveDraft(KEY_A, 'half a question');

    render(
      <StrictMode>
        <Harness draftKey={KEY_A} slotId="s1" />
      </StrictMode>,
    );

    expect(box().value).toBe('half a question');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 50);
    expect(loadDraft(KEY_A)).toBe('half a question');
  });

  it('restores the draft once the signed-in user becomes known', () => {
    saveDraft(KEY_A, 'half a question');
    const { rerender } = render(<Harness draftKey={null} slotId="s1" userId={null} />);
    expect(box().value).toBe('');

    rerender(<Harness draftKey={KEY_A} slotId="s1" />);

    expect(box().value).toBe('half a question');
  });

  it('keeps text typed before the account became known', () => {
    const { rerender } = render(<Harness draftKey={null} slotId="s1" userId={null} />);
    type('typed while loading');

    rerender(<Harness draftKey={KEY_A} slotId="s1" />);

    expect(box().value).toBe('typed while loading');
  });
});

describe('useComposerDraft — saving', () => {
  it('saves once the reader stops typing, not on every key', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);

    type('Wh');
    wait(DRAFT_SAVE_DEBOUNCE_MS - 100);
    type('What is');
    wait(DRAFT_SAVE_DEBOUNCE_MS - 100);
    expect(stored(KEY_A)).toBeNull();

    wait(200);
    expect(loadDraft(KEY_A)).toBe('What is');
  });

  it('saves only the latest text', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);

    type('first');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);
    type('second');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(loadDraft(KEY_A)).toBe('second');
  });

  it('forgets the draft when the box is emptied, for example after sending', () => {
    saveDraft(KEY_A, 'to be sent');
    render(<Harness draftKey={KEY_A} slotId="s1" />);

    type('');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(stored(KEY_A)).toBeNull();
  });

  it('writes what is in the box when the composer goes away before the pause', () => {
    const { unmount } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('leaving now');

    unmount();

    expect(loadDraft(KEY_A)).toBe('leaving now');
  });

  it('writes what is in the box when the page is closed or navigated away from', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('closing the tab');

    window.dispatchEvent(new Event('pagehide'));

    expect(loadDraft(KEY_A)).toBe('closing the tab');
  });

  it('clears the stored draft when the box was emptied and the page is left at once', () => {
    saveDraft(KEY_A, 'to be sent');
    const { unmount } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('');

    unmount();

    expect(stored(KEY_A)).toBeNull();
  });

  it('stores nothing when drafts are off', () => {
    const { unmount } = render(<Harness draftKey={null} slotId="s1" />);
    type('private');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);
    unmount();

    expect(window.localStorage.length).toBe(0);
  });

  it('does not save text the reader did not write, such as an edit or regenerate prompt', () => {
    const { rerender, unmount } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    rerender(<Harness draftKey={KEY_A} slotId="s1" paused />);

    type('the question being edited');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);
    window.dispatchEvent(new Event('pagehide'));
    unmount();

    expect(stored(KEY_A)).toBeNull();
  });

  it('keeps the earlier draft while paused', () => {
    saveDraft(KEY_A, 'my own draft');
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    rerender(<Harness draftKey={KEY_A} slotId="s1" paused />);

    type('edited question');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(loadDraft(KEY_A)).toBe('my own draft');
  });
});

describe('useComposerDraft — moving between conversations', () => {
  it('keeps what was typed in the conversation being left and shows the other one’s draft', () => {
    saveDraft(KEY_B, 'draft in b');
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('typed in a');

    rerender(<Harness draftKey={KEY_B} slotId="s2" />);

    expect(loadDraft(KEY_A)).toBe('typed in a');
    expect(box().value).toBe('draft in b');
  });

  it('shows an empty box in a conversation with no draft, not the previous text', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('typed in a');

    rerender(<Harness draftKey={KEY_B} slotId="s2" />);

    expect(box().value).toBe('');
  });

  it('never files the previous conversation’s text under the new one', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('typed in a');

    rerender(<Harness draftKey={KEY_B} slotId="s2" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS * 3);

    expect(stored(KEY_B)).toBeNull();
  });

  it('brings the draft back when the reader returns', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('typed in a');
    rerender(<Harness draftKey={KEY_B} slotId="s2" />);
    type('typed in b');

    rerender(<Harness draftKey={KEY_A} slotId="s1" />);
    expect(box().value).toBe('typed in a');

    rerender(<Harness draftKey={KEY_B} slotId="s2" />);
    expect(box().value).toBe('typed in b');
  });

  it('keeps a pending edit-mode prompt out of the draft when the reader switches away', () => {
    saveDraft(KEY_A, 'my own draft');
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    rerender(<Harness draftKey={KEY_A} slotId="s1" paused />);
    type('the question being edited');

    // The app leaves edit mode one render after the thread changes.
    rerender(<Harness draftKey={KEY_B} slotId="s2" paused />);
    rerender(<Harness draftKey={KEY_B} slotId="s2" paused={false} />);
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(loadDraft(KEY_A)).toBe('my own draft');
    expect(box().value).toBe('');
    expect(stored(KEY_B)).toBeNull();
  });

  it('keeps typed text when a new chat is given its real id mid-answer', () => {
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId="s1" />);
    type('follow-up while it answers');

    rerender(<Harness draftKey={KEY_A} slotId="s1" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(box().value).toBe('follow-up while it answers');
    expect(loadDraft(KEY_A)).toBe('follow-up while it answers');
    expect(stored(KEY_NEW)).toBeNull();
  });

  it('leaves no draft behind on the new-chat key once the chat has an id', () => {
    saveDraft(KEY_NEW, 'stale');
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId="s1" />);

    rerender(<Harness draftKey={KEY_A} slotId="s1" />);

    expect(stored(KEY_NEW)).toBeNull();
  });
});

describe('useComposerDraft — sending the first message of a new chat', () => {
  it('does not bring the sent text back when the chat gets its thread', () => {
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId={null} />);
    type('my first question');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);
    expect(loadDraft(KEY_NEW)).toBe('my first question');

    type('');
    rerender(<Harness draftKey={KEY_NEW} slotId="s1" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(box().value).toBe('');
    expect(stored(KEY_NEW)).toBeNull();
  });

  it('does not bring the sent text back after leaving and returning to the new chat', () => {
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId={null} />);
    type('my first question');
    type('');
    rerender(<Harness draftKey={KEY_NEW} slotId="s1" />);
    rerender(<Harness draftKey={KEY_A} slotId="s1" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    rerender(<Harness draftKey={KEY_NEW} slotId={null} />);

    expect(box().value).toBe('');
  });

  it('leaves the box alone when only the thread appears and the conversation is unchanged', () => {
    const tooLongToStore = 'x'.repeat(DRAFT_MAX_CHARS + 1);
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId={null} />);
    type(tooLongToStore);

    rerender(<Harness draftKey={KEY_NEW} slotId="s1" />);

    expect(box().value).toBe(tooLongToStore);
  });
});

describe('useComposerDraft — a different person on the same tab', () => {
  const KEY_U2_A = 'pipeshub-chat-draft:u2:-:conv-a';

  it('does not hand one user\u2019s unsent text to the next user', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" userId="u1" />);
    type('private to the first user');

    rerender(<Harness draftKey={KEY_U2_A} slotId="s1" userId="u2" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS * 3);

    expect(box().value).toBe('');
    expect(stored(KEY_U2_A)).toBeNull();
  });

  it('keeps the first user\u2019s text under the first user\u2019s key', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" userId="u1" />);
    type('private to the first user');

    rerender(<Harness draftKey={KEY_U2_A} slotId="s1" userId="u2" />);

    expect(loadDraft(KEY_A)).toBe('private to the first user');
  });

  it('shows the new user\u2019s own draft', () => {
    saveDraft(KEY_U2_A, 'second user draft');
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" userId="u1" />);
    type('private to the first user');

    rerender(<Harness draftKey={KEY_U2_A} slotId="s1" userId="u2" />);

    expect(box().value).toBe('second user draft');
  });

  it('does not carry text across a signed-out gap between two users', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" userId="u1" />);
    type('private to the first user');

    rerender(<Harness draftKey={null} slotId="s1" userId={null} />);
    rerender(<Harness draftKey={KEY_U2_A} slotId="s1" userId="u2" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS * 3);

    expect(box().value).toBe('');
    expect(stored(KEY_U2_A)).toBeNull();
  });

  it('still carries text when the same user\u2019s new chat gets its id', () => {
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId="s1" userId="u1" />);
    type('follow-up while it answers');

    rerender(<Harness draftKey={KEY_A} slotId="s1" userId="u1" />);

    expect(box().value).toBe('follow-up while it answers');
  });
});

describe('useComposerDraft — a message that was just sent', () => {
  const sent = () => act(() => controls?.markSent());

  it('removes the stored draft straight away', () => {
    saveDraft(KEY_A, 'about to be sent');
    render(<Harness draftKey={KEY_A} slotId="s1" />);

    sent();

    expect(stored(KEY_A)).toBeNull();
  });

  it('does not save the sent text when the pause after typing ends', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('about to be sent');

    sent();
    wait(DRAFT_SAVE_DEBOUNCE_MS * 3);

    expect(stored(KEY_A)).toBeNull();
  });

  it('does not save the sent text when the composer goes away while it still holds it', () => {
    const { unmount } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('about to be sent');

    sent();
    unmount();

    expect(stored(KEY_A)).toBeNull();
  });

  it('does not save the sent text when the page is left', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('about to be sent');

    sent();
    window.dispatchEvent(new Event('pagehide'));

    expect(stored(KEY_A)).toBeNull();
  });

  it('does not file the sent text under the id a new chat gets', () => {
    const { rerender } = render(<Harness draftKey={KEY_NEW} slotId="s1" />);
    type('about to be sent');

    sent();
    rerender(<Harness draftKey={KEY_A} slotId="s1" />);
    wait(DRAFT_SAVE_DEBOUNCE_MS * 3);

    expect(stored(KEY_A)).toBeNull();
    expect(stored(KEY_NEW)).toBeNull();
  });

  it('does not keep the sent text when the reader opens another conversation', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('about to be sent');

    sent();
    rerender(<Harness draftKey={KEY_B} slotId="s2" />);

    expect(stored(KEY_A)).toBeNull();
  });

  it('does not bring the sent text back in a composer that replaces this one', () => {
    const first = render(<Harness draftKey={KEY_NEW} slotId={null} />);
    type('about to be sent');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    sent();
    first.unmount();
    render(<Harness draftKey={KEY_NEW} slotId="s1" />);

    expect(box().value).toBe('');
  });

  it('saves again as soon as the reader types something new', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('first message');
    sent();

    type('the next question');
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(loadDraft(KEY_A)).toBe('the next question');
  });

  it('keeps the text as a draft again when the send did not go through', () => {
    render(<Harness draftKey={KEY_A} slotId="s1" />);
    type('refused message');
    sent();

    act(() => controls?.unmarkSent('refused message'));
    wait(DRAFT_SAVE_DEBOUNCE_MS + 10);

    expect(loadDraft(KEY_A)).toBe('refused message');
  });

  it('does not restore a draft for text that is not the reader\u2019s own', () => {
    const { rerender } = render(<Harness draftKey={KEY_A} slotId="s1" />);
    rerender(<Harness draftKey={KEY_A} slotId="s1" paused />);

    act(() => controls?.unmarkSent('edit prompt'));

    expect(stored(KEY_A)).toBeNull();
  });

  it('does nothing harmful when drafts are off', () => {
    render(<Harness draftKey={null} slotId="s1" />);
    type('private');

    expect(() => {
      sent();
      act(() => controls?.unmarkSent('private'));
    }).not.toThrow();
    expect(window.localStorage.length).toBe(0);
  });
});
