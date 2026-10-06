import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { NoteBubble } from '../note-bubble';
import { MentionText, mentionsToPlainText, truncateKeepingTokens } from '../mention-text';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useParticipantsStore } from '@/chat/mentions/participants-store';
import { useChatStore } from '@/chat/store';
import { useUserStore } from '@/lib/store/user-store';
import { rememberMentionLabels } from '../../composer/use-mentionables';

beforeEach(() => {
  useParticipantsStore.getState().reset();
  useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: true } });
});

afterEach(() => {
  cleanup();
  useFeatureFlagsStore.setState({ flags: null });
});

const wrap = (node: React.ReactNode) => render(<Theme>{node}</Theme>);

describe('NoteBubble', () => {
  it('shows the note label, the author and the text, and names itself a note for assistive tech', () => {
    wrap(<NoteBubble content="Remember the deadline" author={{ userId: 'u-bob', displayName: 'Bob Builder' }} meUserId="me" />);
    expect(screen.getByRole('note').getAttribute('aria-label')).toMatch(/not sent to the assistant/i);
    expect(screen.getByText('Note')).toBeTruthy();
    expect(screen.getByTestId('author-chip').textContent).toContain('Bob Builder');
    expect(screen.getByTestId('note-text').textContent).toBe('Remember the deadline');
  });

  it('draws the time in slate-11 (slate-9 on the note background is 3.13:1, axe color-contrast)', () => {
    wrap(<NoteBubble content="x" author={null} timeLabel="1:49 PM" />);
    expect(screen.getByText('1:49 PM').getAttribute('style')).toContain('var(--slate-11)');
  });

  it('draws tokens as chips with the names the chat knows, and "Unknown user" for an id it does not', () => {
    useParticipantsStore.getState().remember([{ ref: { type: 'user', id: 'u-bob' }, label: 'Bob Builder' }]);
    wrap(<NoteBubble content="cc <@user:u-bob> and <@user:u-gone>" author={null} />);
    const chips = screen.getAllByTestId('mention-chip');
    expect(chips.map((c) => c.textContent)).toEqual(['@Bob Builder', '@Unknown user']);
    expect(screen.getByTestId('author-chip').textContent).toContain('Former member');
  });

  it("names the chat's own agent from the agent context, and uses names picked in the popover", () => {
    const initial = useChatStore.getState();
    try {
      const slotId = useChatStore.getState().createSlot('c1');
      useChatStore.getState().updateSlot(slotId, { threadAgentId: 'a1' });
      useChatStore.setState({ activeSlotId: slotId, agentContextDisplayName: 'HR Agent' });
      rememberMentionLabels([{ ref: { type: 'user', id: 'u-carol' }, label: 'Carol' }]);
      wrap(<MentionText text="<@agent:a1> and <@agent:other> ask <@user:u-carol>" />);
      expect(screen.getAllByTestId('mention-chip').map((c) => c.textContent)).toEqual(['@HR Agent', '@Unknown agent', '@Carol']);
    } finally {
      useChatStore.setState(initial, true);
    }
  });

  it('a name arriving later replaces the fallback', () => {
    wrap(<NoteBubble content="<@team:t-1>" />);
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Unknown team');
    act(() => useParticipantsStore.getState().remember([{ ref: { type: 'team', id: 't-1' }, label: 'Sales' }]));
    expect(screen.getByTestId('mention-chip').textContent).toBe('@Sales');
  });

  it('a label is text, never markup', () => {
    useParticipantsStore.getState().remember([{ ref: { type: 'user', id: 'u-x' }, label: '<img src=x onerror=alert(1)>' }]);
    const { container } = wrap(<NoteBubble content="<@user:u-x>" />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByTestId('mention-chip').textContent).toBe('@<img src=x onerror=alert(1)>');
  });
});

describe('MentionText', () => {
  it('an escaped literal is shown as typed and is not a chip', () => {
    wrap(<MentionText text={'type <\\@agent:xyz> literally'} />);
    expect(screen.queryByTestId('mention-chip')).toBeNull();
    expect(document.body.textContent).toContain('type <@agent:xyz> literally');
  });

  it('with the flag off the text is shown exactly as stored', () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: true, ENABLE_CHAT_MENTIONS: false } });
    wrap(<MentionText text="hi <@user:u-bob> <\@x>" />);
    expect(screen.queryByTestId('mention-chip')).toBeNull();
    expect(document.body.textContent).toContain('hi <@user:u-bob> <\\@x>');
  });
});

describe('token helpers', () => {
  it('truncation never leaves half a token', () => {
    const text = 'abcd <@user:u-bob> tail';
    expect(truncateKeepingTokens(text, 8)).toBe('abcd <@user:u-bob>');
    expect(truncateKeepingTokens(text, 4)).toBe('abcd');
    expect(truncateKeepingTokens('short', 100)).toBe('short');
  });

  it('copying gives names, and the unknown wording for a miss', () => {
    expect(mentionsToPlainText('hi <@user:u-bob> <@user:u-gone>', { 'user:u-bob': 'Bob' }, 'someone')).toBe('hi @Bob @someone');
  });

  it('draws a typed reserved assistant alias as an assistant chip, leaving other @words as text', () => {
    wrap(<MentionText text="@assistant help me, cc @pipeshub and mail a@ai.com or @ai.example" />);
    const chips = screen.getAllByTestId('mention-chip');
    expect(chips.map((c) => c.textContent)).toEqual(['@Assistant', '@Assistant']);
    expect(chips.every((c) => c.getAttribute('data-mention-type') === 'assistant')).toBe(true);
  });

  it('draws an alias next to a token chip, and leaves it as text with the flag off', () => {
    wrap(<MentionText text="<@user:u-bob> ask @ai" />);
    expect(screen.getAllByTestId('mention-chip').map((c) => c.getAttribute('data-mention-type'))).toEqual(['user', 'assistant']);
    cleanup();
    useFeatureFlagsStore.setState({ flags: { ENABLE_COLLABORATIVE_CHATS: false, ENABLE_CHAT_MENTIONS: false } });
    wrap(<MentionText text="@assistant help" />);
    expect(screen.queryByTestId('mention-chip')).toBeNull();
  });

  it('shows a mention of the viewer as "@you" with the accessible name "mentions you"', () => {
    useUserStore.setState({ profile: { userId: 'u-me' } as never });
    useParticipantsStore.getState().remember([
      { ref: { type: 'user', id: 'u-me' }, label: 'User Owner' },
      { ref: { type: 'user', id: 'u-bob' }, label: 'Bob Builder' },
    ]);
    wrap(<MentionText text="<@user:u-me> and <@user:u-bob>" />);
    const [mine, other] = screen.getAllByTestId('mention-chip');
    expect(mine.textContent).toBe('@you');
    expect(mine.getAttribute('aria-label')).toBe('mentions you');
    expect(mine.className).toContain('ph-mention-chip--self');
    expect(other.textContent).toBe('@Bob Builder');
    expect(other.getAttribute('aria-label')).toBeNull();
  });
});
