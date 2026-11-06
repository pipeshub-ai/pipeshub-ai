/**
 * The `@` menu lists the sources' roots from one fetch while that answer is fresh. A menu that opens and closes
 * fast, or remounts, asked the server on every open: about 25 requests a second, until the rate limit refused them.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { useChatStore } from '@/chat/store';
import { useAuthStore } from '@/config';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn(), getNodeChildren: vi.fn(), searchNodes: vi.fn() }));

vi.mock('@/chat/api', () => ({
  ChatApi: { listCollectionsForChat: (...args: unknown[]) => api.listCollectionsForChat(...args) },
}));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: {
    getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args),
    searchNodes: (...args: unknown[]) => api.searchNodes(...args),
  },
}));

import { MentionMenu } from '../mention-menu';

const root = (id: string, name: string) => ({ id, name, nodeType: 'app', hasChildren: true, origin: 'CONNECTOR', connector: 'Jira' });
const WAIT = { timeout: 5000 };
let userSeq = 0;

function Menu() {
  return (
    <Theme>
      <MentionMenu query="" onPick={vi.fn()} onClearQuery={vi.fn()} maxHeight={400} listId="menu" onActiveChange={vi.fn()} footer={null} />
    </Theme>
  );
}

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
});

beforeEach(() => {
  window.localStorage.clear();
  useChatStore.setState({ collectionMetaCache: {}, collectionNamesCache: {} });
  // A user of their own per test: the roots are remembered per user, across menus.
  userSeq += 1;
  useAuthStore.setState({ user: { id: `user-${userSeq}` } as never });
  api.listCollectionsForChat.mockResolvedValue({ knowledgeBases: [root('jira', 'Jira')], nextCursor: null });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
  useAuthStore.setState({ user: null });
});

describe('@ menu: requests', () => {
  it('fetches the roots once when the menu opens and closes twenty times', async () => {
    const first = render(<Menu />);
    await screen.findByText('Jira', undefined, WAIT);
    first.unmount();
    for (let i = 0; i < 20; i += 1) {
      const again = render(<Menu />);
      expect(screen.getByText('Jira')).toBeTruthy();
      again.unmount();
    }
    await act(async () => undefined);
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(1);
  });

  it('fetches once for menus that open while the first answer is on its way', async () => {
    let answer: (value: unknown) => void = () => undefined;
    api.listCollectionsForChat.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    render(<Menu />);
    render(<Menu />);
    render(<Menu />);
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(1);
    await act(async () => answer({ knowledgeBases: [root('jira', 'Jira')], nextCursor: null }));
    expect((await screen.findAllByText('Jira', undefined, WAIT)).length).toBe(3);
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(1);
  });

  it('knows the person from the session when the stored profile has no `id` (as the app stores it)', async () => {
    const tokenFor = (userId: string) => `x.${btoa(JSON.stringify({ userId })).replace(/=+$/, '')}.x`;
    useAuthStore.setState({ user: { _id: `profile-${userSeq}` } as never, accessToken: tokenFor(`session-${userSeq}`) } as never);
    const first = render(<Menu />);
    await screen.findByText('Jira', undefined, WAIT);
    first.unmount();
    for (let i = 0; i < 5; i += 1) render(<Menu />).unmount();
    await act(async () => undefined);
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(1);
    useAuthStore.setState({ accessToken: null } as never);
  });

  it('fetches again on an open once the roots are 30 seconds old', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const first = render(<Menu />);
    await screen.findByText('Jira', undefined, WAIT);
    first.unmount();
    vi.setSystemTime(Date.now() + 29_000);
    render(<Menu />).unmount();
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 2_000);
    render(<Menu />);
    await act(async () => undefined);
    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(2);
  });
});
