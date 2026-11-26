/**
 * BGUI-7: "Permissions in this chat" in the chat's top bar (ADR-GRANT-01 D8). The owner of a harness chat sees what
 * "Allow for this chat" allows there, in words, and removes one; the next such call asks again. Nobody else sees it,
 * and a chat that holds none shows no button.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';

const apiClient = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient, streamSSERequest: vi.fn(), streamSSEGet: vi.fn() }));
const viewport = vi.hoisted(() => ({ isMobile: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => viewport.isMobile, useIsBelowDesktop: () => viewport.isMobile }));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/store/toast-store', () => ({ toast: toasts }));

const { useChatStore } = await import('../../../store');
const { useUserStore } = await import('@/lib/store/user-store');
const { ChatGrantsButton, useChatGrantsStore } = await import('..');
const { ConversationHeaderOverlay } = await import('../../conversation-header-overlay');

const h = React.createElement;
const SESSION = 'ses-1';
const initialState = useChatStore.getState();

const grant = (id: string, patch: Record<string, unknown> = {}) => ({
  grantId: `cg_${id.repeat(40)}`,
  toolId: 'slack@i/send_message',
  tool: { app: 'slack', name: 'send_message' },
  destination: '#sales',
  sensitivity: 'public',
  grantedBy: 'u-me',
  grantedByName: 'Ana Me',
  grantedAt: '2026-11-08T10:00:00Z',
  ...patch,
});

let held: ReturnType<typeof grant>[] = [];

function chat(patch: Record<string, unknown> = {}) {
  const slotId = useChatStore.getState().activeSlotId as string;
  useChatStore.getState().updateSlot(slotId, { isOwner: true, durable: true, ...patch });
}

const wrap = (node: React.ReactNode) => render(h(Theme, null, node));
const button = () => screen.queryByRole('button', { name: 'Permissions in this chat' });

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

beforeEach(() => {
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null }, true);
  useChatGrantsStore.setState({ byChat: {}, removing: {}, openFor: null });
  useUserStore.setState({ profile: { userId: 'u-me', firstName: 'Ana', lastName: 'Me', fullName: 'Ana Me', email: null, isAdmin: false, avatarUrl: null, hasLoggedIn: true } });
  viewport.isMobile = false;
  held = [grant('a'), grant('b', { toolId: 'web@i/fetch', tool: { app: 'web', name: 'fetch' }, destination: 'onyx.app' })];
  apiClient.get.mockReset().mockImplementation(async (url: string) => {
    if (url.endsWith(`/conversations/${SESSION}/grants`)) return { data: { grants: held } };
    throw new Error(`unexpected GET ${url}`);
  });
  apiClient.delete.mockReset().mockImplementation(async (url: string) => {
    const id = decodeURIComponent(url.split('/').pop() ?? '');
    const gone = held.find((g) => g.grantId === id);
    held = held.filter((g) => g.grantId !== id);
    return { data: { revoked: gone, runs: 2 } };
  });
  toasts.success.mockReset();
  toasts.error.mockReset();
  const slotId = useChatStore.getState().createSlot(SESSION);
  useChatStore.setState({ activeSlotId: slotId });
});

afterEach(() => cleanup());

describe('Permissions in this chat', () => {
  it("the owner's chat with grants shows the button; opening it lists each in words, with when", async () => {
    chat();
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(button()).not.toBeNull());
    fireEvent.click(button() as HTMLElement);
    const panel = await screen.findByTestId('chat-grants-panel');
    const rows = within(panel).getAllByTestId('chat-grant');
    expect(rows.map((r) => r.textContent)).toEqual([
      'how_to_regSlack: Send message to #salesAllowed on Nov 8Remove',
      'how_to_regWeb: Fetch to onyx.appAllowed on Nov 8Remove',
    ]);
    expect(within(panel).getByRole('button', { name: 'Remove Slack: Send message to #sales' })).toBeTruthy();
  });

  it('names who allowed each one only in a shared chat, "you" for your own', async () => {
    held = [grant('a'), grant('b', { grantedBy: 'u-2', grantedByName: 'Priya Nair', destination: '#ops' })];
    chat({ isShared: true });
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(button()).not.toBeNull());
    fireEvent.click(button() as HTMLElement);
    const rows = within(await screen.findByTestId('chat-grants-panel')).getAllByTestId('chat-grant');
    expect(rows[0]!.textContent).toContain('Allowed by you on Nov 8');
    expect(rows[1]!.textContent).toContain('Allowed by Priya Nair on Nov 8');
  });

  it('no grants, no button; not the owner, nothing is read; not a harness chat, nothing is read', async () => {
    held = [];
    chat();
    const view = wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(1));
    expect(button()).toBeNull();
    view.unmount();

    apiClient.get.mockClear();
    held = [grant('a')];
    chat({ isOwner: false });
    wrap(h(ChatGrantsButton, { isMobile: false }));
    chat({ isOwner: false, durable: false });
    await act(async () => {});
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(button()).toBeNull();
  });

  it('Remove takes it back: the row goes at once, the toast says the next call asks again, the list is read again', async () => {
    chat();
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(button()).not.toBeNull());
    fireEvent.click(button() as HTMLElement);
    const panel = await screen.findByTestId('chat-grants-panel');
    const reads = apiClient.get.mock.calls.length;
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Slack: Send message to #sales' }));
    await waitFor(() => expect(within(panel).getAllByTestId('chat-grant')).toHaveLength(1));
    expect(apiClient.delete).toHaveBeenCalledWith(`/api/v1/conversations/${SESSION}/grants/cg_${'a'.repeat(40)}`, expect.anything());
    expect(toasts.success).toHaveBeenCalledWith('Removed: Slack: Send message to #sales', { description: 'It asks you again the next time.' });
    await waitFor(() => expect(apiClient.get.mock.calls.length).toBe(reads + 1));
  });

  it('removing the last one keeps the open list, which then says nothing is allowed; closed, the button goes', async () => {
    held = [grant('a')];
    chat();
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(button()).not.toBeNull());
    fireEvent.click(button() as HTMLElement);
    const panel = await screen.findByTestId('chat-grants-panel');
    fireEvent.click(within(panel).getByRole('button', { name: /^Remove / }));
    expect(await within(panel).findByTestId('chat-grants-empty')).toBeTruthy();
    expect(button()).not.toBeNull();
    act(() => useChatGrantsStore.getState().close());
    await waitFor(() => expect(button()).toBeNull());
  });

  it('a failed remove keeps the row and says it is still on; one already gone elsewhere counts as removed', async () => {
    chat();
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(button()).not.toBeNull());
    fireEvent.click(button() as HTMLElement);
    const panel = await screen.findByTestId('chat-grants-panel');

    apiClient.delete.mockRejectedValueOnce(Object.assign(new Error('boom'), { type: 'SERVER_ERROR', statusCode: 500 }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Slack: Send message to #sales' }));
    await waitFor(() => expect(toasts.error).toHaveBeenCalledWith('That permission is still on. Try again.'));
    expect(within(panel).getAllByTestId('chat-grant')).toHaveLength(2);

    held = [held[1]!];
    apiClient.delete.mockRejectedValueOnce(Object.assign(new Error('gone'), { type: 'NOT_FOUND', statusCode: 404 }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Slack: Send message to #sales' }));
    await waitFor(() => expect(within(panel).getAllByTestId('chat-grant')).toHaveLength(1));
    expect(toasts.success).toHaveBeenCalled();
  });

  it('a read that started before a remove does not bring the removed grant back', async () => {
    const session = { sessionId: SESSION };
    let release: (v: unknown) => void = () => {};
    apiClient.get.mockImplementationOnce(() => new Promise((r) => (release = r)));
    const slow = useChatGrantsStore.getState().load(session);
    useChatGrantsStore.setState({ byChat: { [SESSION]: held as never } });
    await useChatGrantsStore.getState().revoke(session, `cg_${'a'.repeat(40)}`);
    release({ data: { grants: [grant('a'), grant('b')] } });
    await slow;
    await waitFor(() => expect(useChatGrantsStore.getState().byChat[SESSION]?.map((g) => g.grantId)).toEqual([`cg_${'b'.repeat(40)}`]));
  });

  it('an ask answered in the chat reads the list again, so a grant just given shows', async () => {
    held = [];
    chat({ pendingApprovals: [{ interactionId: 'i-1' }] });
    wrap(h(ChatGrantsButton, { isMobile: false }));
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(1));
    expect(button()).toBeNull();
    held = [grant('a')];
    act(() => chat({ pendingApprovals: [] }));
    await waitFor(() => expect(button()).not.toBeNull());
  });

  it('on a phone it opens a bottom sheet with 44 px Remove buttons, and sits in the top bar', async () => {
    viewport.isMobile = true;
    chat();
    wrap(h(ConversationHeaderOverlay, { conversationId: SESSION, agentChip: null, shareGroup: null, grants: h(ChatGrantsButton, { isMobile: true }) }));
    await waitFor(() => expect(button()).not.toBeNull());
    expect((button() as HTMLElement).style.minHeight).toBe('44px');
    fireEvent.click(button() as HTMLElement);
    const panel = await screen.findByTestId('chat-grants-panel');
    const remove = within(panel).getAllByRole('button', { name: /^Remove / })[0] as HTMLElement;
    expect(remove.style.minHeight).toBe('44px');
  });
});
