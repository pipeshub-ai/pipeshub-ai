import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { en } from '@/lib/__tests__/test-i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/lib/store/user-store', () => ({
  useUserStore: (sel: (s: unknown) => unknown) => sel({}),
  selectIsProfileInitialized: () => true,
}));
vi.mock('@/chat/components/chat-panel/expansion-panels/connectors-collections', () => ({
  CollectionsTab: ({ onSelectionChange }: { onSelectionChange: (next: unknown) => void }) => (
    <button type="button" onClick={() => onSelectionChange({ apps: ['a9'], kb: [], recordGroups: [], records: [] })}>
      pick a9
    </button>
  ),
}));
const updateKnowledgeLimit = vi.hoisted(() => vi.fn());
const listed = vi.hoisted(() => ({
  tokens: [
    { id: 't1', name: 'No scopes token', createdAt: '2026-01-01', expiresAt: '2026-06-01' },
    {
      id: 't2',
      name: 'CI token',
      scopes: ['kb:read'],
      createdAt: '2026-01-01',
      expiresAt: '2026-06-01',
      knowledgeLimit: { apps: ['a1'], recordGroups: [], records: [] },
    },
  ],
}));
const listTokens = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({
  PatApi: {
    listTokens,
    getScopes: vi.fn().mockResolvedValue({ scopes: [] }),
    revokeToken: vi.fn(),
    updateKnowledgeLimit,
  },
  AgentSignInApi: { list: vi.fn().mockResolvedValue([]), revoke: vi.fn() },
}));

const access = en.workspace.personalAccessTokens.access;

import PersonalAccessTokensPage from '../page';

beforeEach(() => {
  listTokens.mockReset();
  listTokens.mockResolvedValue(listed);
});
afterEach(cleanup);

describe('personal access tokens page', () => {
  it('lists a token whose scopes are missing', async () => {
    installBrowserShims();
    renderInTheme(<PersonalAccessTokensPage />);
    expect(await screen.findByText('No scopes token')).toBeTruthy();
  });

  it("changes what a token can see from its row and shows the new choice there", async () => {
    installBrowserShims();
    updateKnowledgeLimit.mockResolvedValue({
      token: { id: 't2', name: 'CI token', scopes: ['kb:read'], createdAt: '2026-01-01', expiresAt: '2026-06-01' },
    });
    renderInTheme(<PersonalAccessTokensPage />);
    await screen.findByText('CI token');
    expect(screen.getByText('Can see: 1 picked item')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Change what CI token can see' }));
    expect((await screen.findAllByText(access.panelTitle)).length).toBeGreaterThan(0);
    fireEvent.click(document.getElementById('pat-access-knowledge-limit-everything')!);
    fireEvent.click(screen.getByRole('button', { name: access.save }));

    await waitFor(() => expect(updateKnowledgeLimit).toHaveBeenCalledWith('t2', null));
    await waitFor(() => expect(screen.queryByText('Can see: 1 picked item')).toBeNull());
    expect(screen.getAllByText('Can see: everything you can')).toHaveLength(2);
  });

  it('shows the restored access in a reopened panel after Undo', async () => {
    installBrowserShims();
    const ci = {
      id: 't2', name: 'CI token', scopes: ['kb:read'], createdAt: '2026-01-01', expiresAt: '2026-06-01',
      knowledgeLimit: { apps: ['a1'], recordGroups: [], records: [] },
    };
    useToastStore.setState({ toasts: [] });
    updateKnowledgeLimit.mockReset();
    updateKnowledgeLimit
      .mockResolvedValueOnce({ token: { ...ci, knowledgeLimit: undefined } })
      .mockResolvedValueOnce({ token: ci });
    renderInTheme(<PersonalAccessTokensPage />);
    await screen.findByText('CI token');
    const radio = (choice: string) =>
      document.getElementById(`pat-access-knowledge-limit-${choice}`) as HTMLInputElement;

    fireEvent.click(screen.getByRole('button', { name: 'Change what CI token can see' }));
    fireEvent.click(radio('everything'));
    fireEvent.click(screen.getByRole('button', { name: access.save }));
    await waitFor(() => expect(useToastStore.getState().toasts.at(-1)?.action).toBeTruthy());
    const undo = useToastStore.getState().toasts.at(-1)?.action;

    fireEvent.click(screen.getByRole('button', { name: 'Change what CI token can see' }));
    await waitFor(() => expect(radio('everything').checked).toBe(true));
    await act(async () => {
      undo?.onClick?.();
    });

    await waitFor(() => expect(radio('only').checked).toBe(true));
  });

  it('does not let an older reload undo a newer change in the list', async () => {
    installBrowserShims();
    useToastStore.setState({ toasts: [] });
    const ci = listed.tokens[1];
    let finishReload: (value: unknown) => void = () => {};
    listTokens
      .mockResolvedValueOnce(listed)
      .mockReturnValueOnce(new Promise((resolve) => { finishReload = resolve; }));
    updateKnowledgeLimit.mockReset();
    updateKnowledgeLimit
      .mockResolvedValueOnce({ token: { ...ci, knowledgeLimit: undefined } })
      .mockRejectedValueOnce({ type: 'NETWORK_ERROR', message: 'Network error.' })
      .mockResolvedValueOnce({ token: ci });
    renderInTheme(<PersonalAccessTokensPage />);
    await screen.findByText('CI token');
    const radio = (choice: string) =>
      document.getElementById(`pat-access-knowledge-limit-${choice}`) as HTMLInputElement;
    const openCi = () => fireEvent.click(screen.getByRole('button', { name: 'Change what CI token can see' }));

    // A first change succeeds and offers Undo.
    openCi();
    fireEvent.click(radio('everything'));
    fireEvent.click(screen.getByRole('button', { name: access.save }));
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.action).toBeTruthy());
    const undo = useToastStore.getState().toasts[0]?.action;

    // A second change gets no answer, so the page starts re-reading the list.
    openCi();
    fireEvent.click(radio('only'));
    fireEvent.click(screen.getByText('pick a9'));
    fireEvent.click(screen.getByRole('button', { name: access.save }));
    await waitFor(() => expect(listTokens).toHaveBeenCalledTimes(2));

    // The first change is undone while that read is still out, then the read lands.
    await act(async () => {
      undo?.onClick?.();
    });
    await waitFor(() => expect(screen.getByText('Can see: 1 picked item')).toBeTruthy());
    await act(async () => {
      finishReload({ tokens: [listed.tokens[0], { ...ci, knowledgeLimit: undefined }] });
    });

    expect(screen.getByText('Can see: 1 picked item')).toBeTruthy();
  });

  it('reads the list again when a change lands during a read, so a revoked token still leaves it', async () => {
    installBrowserShims();
    useToastStore.setState({ toasts: [] });
    const ci = listed.tokens[1];
    let finishRead: (value: unknown) => void = () => {};
    listTokens
      .mockResolvedValueOnce(listed)
      .mockReturnValueOnce(new Promise((resolve) => { finishRead = resolve; }))
      .mockResolvedValueOnce({ tokens: [ci] });
    updateKnowledgeLimit.mockReset();
    updateKnowledgeLimit
      .mockResolvedValueOnce({ token: { ...ci, knowledgeLimit: undefined } })
      .mockResolvedValueOnce({ token: ci });
    renderInTheme(<PersonalAccessTokensPage />);
    await screen.findByText('CI token');

    fireEvent.click(screen.getByRole('button', { name: 'Change what CI token can see' }));
    fireEvent.click(document.getElementById('pat-access-knowledge-limit-everything')!);
    fireEvent.click(screen.getByRole('button', { name: access.save }));
    await waitFor(() => expect(useToastStore.getState().toasts[0]?.action).toBeTruthy());
    const undo = useToastStore.getState().toasts[0]?.action;

    // Revoking starts a list read; Undo is confirmed while that read is still out.
    fireEvent.click(screen.getAllByRole('button', { name: 'Revoke No scopes token' })[0]);
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(listTokens).toHaveBeenCalledTimes(2));
    await act(async () => {
      undo?.onClick?.();
    });
    await act(async () => {
      finishRead({ tokens: [{ ...ci, knowledgeLimit: undefined }] });
    });

    await waitFor(() => expect(listTokens).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.queryByText('No scopes token')).toBeNull());
    expect(screen.getByText('Can see: 1 picked item')).toBeTruthy();
  });

  it('tells you a new token\'s access can be changed later', async () => {
    installBrowserShims();
    renderInTheme(<PersonalAccessTokensPage />);
    await screen.findByText('CI token');
    fireEvent.click(screen.getByRole('button', { name: en.workspace.personalAccessTokens.newToken }));
    expect(await screen.findByText(en.workspace.personalAccessTokens.create.knowledgeLimitLaterHint)).toBeTruthy();
  });
});
