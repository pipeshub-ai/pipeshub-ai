import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { en } from '@/lib/__tests__/test-i18n';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';
import { Theme } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { ErrorType, type ProcessedError } from '@/lib/api/api-error';
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
// The store puts the newest toast first.
const lastToast = () => useToastStore.getState().toasts[0];
/** Presses the newest toast's action as its button does: runs it, then closes that toast, so one waiting behind it shows. */
const pressToastAction = () => {
  const toast = lastToast();
  toast?.action?.onClick?.();
  if (toast) useToastStore.getState().removeToast(toast.id);
};

function open(t: PatListItem = token) {
  const onSaved = vi.fn();
  const onOpenChange = vi.fn();
  const onReload = vi.fn().mockResolvedValue(undefined);
  const view = renderInTheme(
    <EditPatAccessPanel token={t} onOpenChange={onOpenChange} onSaved={onSaved} onReload={onReload} />
  );
  const show = (next: PatListItem | null) =>
    view.rerender(
      <Theme>
        <EditPatAccessPanel token={next} onOpenChange={onOpenChange} onSaved={onSaved} onReload={onReload} />
      </Theme>
    );
  return { onSaved, onOpenChange, onReload, show };
}

const refused: ProcessedError = { type: ErrorType.VALIDATION_ERROR, statusCode: 400, message: 'bad' };
const noAnswer: ProcessedError = { type: ErrorType.NETWORK_ERROR, message: 'Network error.' };

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

  it("does not carry one token's picks to the next token opened in the same panel", () => {
    const { show } = open();
    show({ ...token, id: 't2', name: 'Docs token', knowledgeLimit: undefined });
    expect(everything().checked).toBe(true);

    fireEvent.click(only());

    expect(only().checked).toBe(true);
    // Nothing is picked for this token yet, so there is nothing to save.
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
      pressToastAction();
    });

    await waitFor(() => expect(onSaved).toHaveBeenLastCalledWith(token));
    expect(updateKnowledgeLimit).toHaveBeenLastCalledWith('t1', limit);
  });

  it('does not close another token the user opened while a save was still going', async () => {
    let finish: (value: { token: PatListItem }) => void = () => {};
    updateKnowledgeLimit.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const { onSaved, onOpenChange, show } = open();

    fireEvent.click(everything());
    fireEvent.click(save());
    show({ ...token, id: 't9', name: 'Other token' });
    await act(async () => {
      finish({ token: { ...token, knowledgeLimit: undefined } });
    });

    expect(onSaved).toHaveBeenCalledWith({ ...token, knowledgeLimit: undefined });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('does not close the same token reopened while its earlier save was still going', async () => {
    let finish: (value: { token: PatListItem }) => void = () => {};
    updateKnowledgeLimit.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const { onOpenChange, show } = open();

    fireEvent.click(everything());
    fireEvent.click(save());
    show(null);
    show(token);
    await act(async () => {
      finish({ token: { ...token, knowledgeLimit: undefined } });
    });

    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("follows the token's access when the server reports a new one while open", () => {
    const { show } = open();
    expect(only().checked).toBe(true);

    show({ ...token, knowledgeLimit: undefined });

    expect(everything().checked).toBe(true);
    expect(save().disabled).toBe(true);
  });

  it('keeps the panel open and says nothing changed when the server refuses', async () => {
    updateKnowledgeLimit.mockRejectedValue(refused);
    const { onSaved, onOpenChange, onReload } = open();

    fireEvent.click(everything());
    fireEvent.click(save());

    await waitFor(() => expect(lastToast()?.variant).toBe('error'));
    expect(lastToast()?.title).toBe(text.errorTitle);
    expect(lastToast()?.description).toBe(text.errorDescription);
    expect(onSaved).not.toHaveBeenCalled();
    expect(onReload).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("reloads the token and does not claim nothing changed when no answer came back", async () => {
    updateKnowledgeLimit.mockRejectedValue(noAnswer);
    const { onOpenChange, onReload } = open();

    fireEvent.click(everything());
    fireEvent.click(save());

    await waitFor(() => expect(onReload).toHaveBeenCalled());
    await waitFor(() => expect(lastToast()?.title).toBe(text.unknownTitle));
    expect(lastToast()?.description).toBe(text.unknownDescription);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it('reloads the token when an Undo gets no answer', async () => {
    updateKnowledgeLimit
      .mockResolvedValueOnce({ token: { ...token, knowledgeLimit: undefined } })
      .mockRejectedValueOnce(noAnswer);
    const { onReload } = open();

    fireEvent.click(everything());
    fireEvent.click(save());
    await waitFor(() => expect(lastToast()?.action).toBeTruthy());
    await act(async () => {
      pressToastAction();
    });

    await waitFor(() => expect(onReload).toHaveBeenCalled());
    await waitFor(() => expect(lastToast()?.title).toBe(text.unknownTitle));
  });

  it("keeps a reopened panel's new choice when an earlier save then lands", async () => {
    let finish: (value: { token: PatListItem }) => void = () => {};
    updateKnowledgeLimit.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    // A parent that keeps the token as the server last reported it, as the page does.
    function Parent() {
      const [current, setCurrent] = React.useState<PatListItem | null>(token);
      return (
        <>
          <button type="button" onClick={() => setCurrent(null)}>close</button>
          <button type="button" onClick={() => setCurrent((c) => c ?? token)}>reopen</button>
          <EditPatAccessPanel token={current} onOpenChange={() => {}} onSaved={setCurrent} onReload={async () => {}} />
        </>
      );
    }
    renderInTheme(<Parent />);

    fireEvent.click(everything());
    fireEvent.click(save());
    fireEvent.click(screen.getByText('close'));
    fireEvent.click(screen.getByText('reopen'));
    expect(only().checked).toBe(true);
    fireEvent.click(everything());
    await act(async () => {
      finish({ token: { ...token, knowledgeLimit: { apps: ['app-9'], recordGroups: [], records: [] } } });
    });

    expect(everything().checked).toBe(true);
    expect(save().disabled).toBe(false);
  });

  it('says to reload when the re-read after an unknown outcome fails too', async () => {
    updateKnowledgeLimit.mockRejectedValue(noAnswer);
    const { onReload } = open();
    onReload.mockRejectedValue(noAnswer);

    fireEvent.click(everything());
    fireEvent.click(save());

    await waitFor(() => expect(lastToast()?.title).toBe(text.unknownTitle));
    expect(lastToast()?.description).toBe(text.unknownReloadFailedDescription);
  });
});
