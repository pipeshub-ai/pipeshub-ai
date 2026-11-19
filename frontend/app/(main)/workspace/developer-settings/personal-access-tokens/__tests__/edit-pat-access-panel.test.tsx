import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { en } from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { Theme } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import type { PatListItem } from '../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/chat/components/chat-panel/expansion-panels/connectors-collections', () => ({
  CollectionsTab: () => null,
}));

const updateKnowledgeLimit = vi.hoisted(() => vi.fn());
vi.mock('../api', () => ({ PatApi: { updateKnowledgeLimit } }));

import { EditPatAccessPanel } from '../components/edit-pat-access-panel';

const text = en.workspace.personalAccessTokens.access;
const limit = { apps: ['app-1'], recordGroups: [], records: [] };
const token: PatListItem = {
  id: 't1',
  name: 'CI token',
  scopes: ['kb:read'],
  createdAt: '2026-01-01',
  expiresAt: '2026-06-01',
  knowledgeLimit: limit,
};

const everything = () => document.getElementById('pat-access-knowledge-limit-everything') as HTMLInputElement;
const only = () => document.getElementById('pat-access-knowledge-limit-only') as HTMLInputElement;
const save = () => screen.getByRole('button', { name: text.save }) as HTMLButtonElement;
const lastToast = () => useToastStore.getState().toasts.at(-1);

function open(t: PatListItem = token) {
  const onSaved = vi.fn();
  const onOpenChange = vi.fn();
  renderInTheme(<EditPatAccessPanel token={t} onOpenChange={onOpenChange} onSaved={onSaved} />);
  return { onSaved, onOpenChange };
}

describe('Changing what a personal access token can see', () => {
  beforeEach(() => {
    installBrowserShims();
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => {
    cleanup();
    updateKnowledgeLimit.mockReset();
  });

  it("opens on the token's current choice, with Save off until something changes", () => {
    open();
    expect(only().checked).toBe(true);
    expect(save().disabled).toBe(true);
  });

  it('opens on everything for a token with no limit', () => {
    open({ ...token, knowledgeLimit: undefined });
    expect(everything().checked).toBe(true);
    expect(save().disabled).toBe(true);
  });

  it('saves the new choice for this token and says what it can see now', async () => {
    updateKnowledgeLimit.mockResolvedValue({ token: { ...token, knowledgeLimit: undefined } });
    const { onSaved, onOpenChange } = open();

    fireEvent.click(everything());
    expect(save().disabled).toBe(false);
    fireEvent.click(save());

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ ...token, knowledgeLimit: undefined }));
    expect(updateKnowledgeLimit).toHaveBeenCalledWith('t1', null);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(lastToast()?.variant).toBe('success');
    expect(lastToast()?.title).toBe(text.savedTitle);
    expect(lastToast()?.description).toBe(text.savedEverything.replace('{{name}}', 'CI token'));
    expect(lastToast()?.action?.label).toBe(text.undo);
  });

  it('puts the previous choice back on Undo', async () => {
    updateKnowledgeLimit
      .mockResolvedValueOnce({ token: { ...token, knowledgeLimit: undefined } })
      .mockResolvedValueOnce({ token });
    const { onSaved } = open();

    fireEvent.click(everything());
    fireEvent.click(save());
    await waitFor(() => expect(lastToast()?.action).toBeTruthy());

    await act(async () => {
      lastToast()?.action?.onClick?.();
    });

    await waitFor(() => expect(onSaved).toHaveBeenLastCalledWith(token));
    expect(updateKnowledgeLimit).toHaveBeenLastCalledWith('t1', limit);
  });

  it('does not close another token the user opened while a save was still going', async () => {
    let finish: (value: { token: PatListItem }) => void = () => {};
    updateKnowledgeLimit.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const onSaved = vi.fn();
    const onOpenChange = vi.fn();
    const { rerender } = renderInTheme(
      <EditPatAccessPanel token={token} onOpenChange={onOpenChange} onSaved={onSaved} />
    );

    fireEvent.click(everything());
    fireEvent.click(save());
    const other: PatListItem = { ...token, id: 't9', name: 'Other token' };
    rerender(
      <Theme>
        <EditPatAccessPanel token={other} onOpenChange={onOpenChange} onSaved={onSaved} />
      </Theme>
    );
    await act(async () => {
      finish({ token: { ...token, knowledgeLimit: undefined } });
    });

    expect(onSaved).toHaveBeenCalledWith({ ...token, knowledgeLimit: undefined });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('keeps the panel open and says so when the change fails', async () => {
    updateKnowledgeLimit.mockRejectedValue(new Error('boom'));
    const { onSaved, onOpenChange } = open();

    fireEvent.click(everything());
    fireEvent.click(save());

    await waitFor(() => expect(lastToast()?.variant).toBe('error'));
    expect(lastToast()?.title).toBe(text.errorTitle);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
