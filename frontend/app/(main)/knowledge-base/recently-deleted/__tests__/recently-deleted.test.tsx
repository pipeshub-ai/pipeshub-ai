/**
 * The Recently deleted page: what each row says, and what a person is told
 * after a restore (all back, some back, none back, and why). The backend is
 * faked at the axios adapter.
 */
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import testI18n from '@/lib/__tests__/test-i18n';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';
import type { TrashItem, TrashListResponse } from '../types';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { useToastStore } = await import('@/lib/store/toast-store');
const { installBrowserShims, renderInTheme } = await import('../../__tests__/kb-page-harness');
const { RecentlyDeletedView } = await import('../components/recently-deleted-view');
const utils = await import('../utils');

const t = testI18n.t.bind(testI18n);
const KB = '/api/v1/knowledgeBase';
const KB_ID = 'kb-1';
const DAY = 24 * 60 * 60 * 1000;
const DELETED_AT = Date.UTC(2026, 9, 1, 12);
const toasts = () => useToastStore.getState().toasts.map((x) => ({ variant: x.variant, title: x.title, description: x.description }));

function item(id: string, overrides: Partial<TrashItem> = {}): TrashItem {
  return {
    id,
    name: `${id}.pdf`,
    isFolder: false,
    mimeType: 'application/pdf',
    parentId: null,
    parentName: null,
    parentInTrash: false,
    itemCount: 1,
    deletedAtTimestamp: DELETED_AT,
    deletedBy: { name: 'Ada Admin', email: 'ada@acme.test' },
    removableAfterTimestamp: DELETED_AT + 14 * DAY,
    ...overrides,
  };
}

function page(items: TrashItem[], overrides: Partial<TrashListResponse> = {}): TrashListResponse {
  return {
    success: true,
    items,
    pagination: { page: 1, limit: 25, totalCount: items.length, totalPages: items.length ? 1 : 0 },
    retention: { minAgeMs: 14 * DAY },
    ...overrides,
  };
}

const docs = item('docs', { name: 'Docs', isFolder: true, mimeType: null, itemCount: 4 });
const inDocs = item('a', { name: 'a.pdf', parentId: 'docs', parentName: 'Docs', parentInTrash: true });

describe('what a row says', () => {
  it('counts what a folder took with it, and a file its attachments', () => {
    expect(utils.itemTypeLabel(docs, t)).toBe('Folder with 3 items');
    expect(utils.itemTypeLabel(item('f', { itemCount: 2 }), t)).toBe('File with 1 attachment');
    expect(utils.itemTypeLabel(item('f'), t)).toBe('File');
    expect(utils.itemTypeLabel(item('e', { isFolder: true, itemCount: 1 }), t)).toBe('Folder');
  });

  it('places an item in its folder, or at the collection top level', () => {
    expect(utils.locationLabel(inDocs, 'Handbook', t)).toBe('Docs');
    expect(utils.locationLabel(item('r'), 'Handbook', t)).toBe('Handbook');
    expect(utils.locationLabel(item('r'), '', t)).toBe('Top level');
  });

  it('names who deleted it, falling back to their email, then to "Unknown"', () => {
    expect(utils.deletedByLabel(item('r'), t)).toBe('Ada Admin');
    expect(utils.deletedByLabel(item('r', { deletedBy: { name: ' ', email: 'max@acme.test' } }), t)).toBe('max@acme.test');
    expect(utils.deletedByLabel(item('r', { deletedBy: null }), t)).toBe('Unknown');
  });

  it('gives the date from which it may be removed for good, or the default 14 days', () => {
    const date = utils.formatTrashDate(DELETED_AT + 14 * DAY, 'en-US');
    expect(utils.removalLabel(item('r'), t, 'en-US')).toBe(`After ${date}`);
    expect(utils.removalLabel(item('r', { removableAfterTimestamp: null }), t)).toBe('After 14 days');
  });

  it('states the retention the server reports, and 14 days when it reports none', () => {
    expect(utils.retentionNote({ minAgeMs: 21 * DAY }, t)).toContain('at least 21 days');
    expect(utils.retentionNote(null, t)).toContain('at least 14 days');
    expect(utils.retentionNote({ minAgeMs: 5 * 60 * 1000 }, t)).toContain('at least 14 days');
  });
});

describe('what a restore says', () => {
  it('says a single file is back and when search catches up', () => {
    const message = utils.restoreMessage(utils.outcomeOfSingle(item('r'), { success: true, restoredRecords: [{ recordId: 'r' }] }, t), t);
    expect(message).toEqual({
      variant: 'success',
      title: "Restored 'r.pdf'",
      description: 'Restored items are back where they were, and show up in search again once indexing finishes.',
    });
  });

  it('shows a pending re-index in plain words, with any rename', () => {
    const outcome = utils.outcomeOfSingle(item('r'), {
      success: true,
      reindexPending: true,
      restoredRecords: [{ recordId: 'r', name: 'r (restored).pdf', renamedFrom: 'r.pdf' }],
    }, t);
    expect(utils.restoreMessage(outcome, t).description).toBe(
      "Restored; it will be searchable again shortly.\n'r.pdf' came back as 'r (restored).pdf', because another item there already has that name.",
    );
  });

  it('passes on why the server refused, and falls back to plain words by status', () => {
    const folderFirst = { type: 'CONFLICT', statusCode: 409, message: "'a.pdf' was in 'Docs', which is also in the trash. Restore 'Docs' first, then restore 'a.pdf'." };
    expect(utils.restoreMessage(utils.outcomeOfSingleError(inDocs, folderFirst, t), t)).toEqual({
      variant: 'error',
      title: "Couldn't restore 'a.pdf'",
      description: folderFirst.message,
    });
    const bare403 = { type: 'AUTHORIZATION_ERROR', statusCode: 403, message: '' };
    expect(utils.reasonFromError(bare403, t)).toBe(
      "You don't have permission to restore this item. Ask the collection's owner for edit access.",
    );
  });

  it('lists what did not come back in a partial bulk restore', () => {
    const outcome = utils.outcomeOfBulk([docs, item('x')], {
      success: false,
      restoredCount: 4,
      failedCount: 1,
      results: [
        { recordId: 'docs', success: true, restoredRecords: [{ recordId: 'docs' }] },
        { recordId: 'x', success: false, code: 403, reason: 'You need edit access to this collection to restore this item.' },
      ],
    }, t);
    expect(utils.restoreMessage(outcome, t)).toEqual({
      variant: 'warning',
      title: 'Restored 1 of 2 items',
      description: "'x.pdf': You need edit access to this collection to restore this item.\n" +
        'Restored items are back where they were, and show up in search again once indexing finishes.',
    });
  });

  it('says none came back, and counts the reasons past the first three', () => {
    const items = ['a', 'b', 'c', 'd', 'e'].map((id) => item(id));
    const outcome = utils.outcomeOfBulkError(items, { type: 'SERVER_ERROR', statusCode: 500, message: '' }, t);
    const message = utils.restoreMessage(outcome, t);
    expect(message.variant).toBe('error');
    expect(message.title).toBe('None of the 5 items could be restored');
    expect(message.description.split('\n')).toEqual([
      "'a.pdf': We couldn't restore this item. Try again in a moment.",
      "'b.pdf': We couldn't restore this item. Try again in a moment.",
      "'c.pdf': We couldn't restore this item. Try again in a moment.",
      '…and 2 more',
    ]);
  });

  it('restores a folder before what was deleted from inside it', () => {
    expect(utils.orderForRestore([inDocs, item('solo'), docs]).map((i) => i.id)).toEqual(['solo', 'docs', 'a']);
  });

  it('goes back to the last page that still has rows', () => {
    expect(utils.pageAfterRemoval(3, 25, 51)).toBe(3);
    expect(utils.pageAfterRemoval(3, 25, 50)).toBe(2);
    expect(utils.pageAfterRemoval(2, 25, 0)).toBe(1);
  });
});

describe('the page', () => {
  beforeEach(() => {
    installBrowserShims();
    useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r' });
    useToastStore.setState({ toasts: [] });
    router.push.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function render(routes: Parameters<typeof fakeApi>[0]) {
    const api = fakeApi({ [`GET ${KB}/${KB_ID}`]: { status: 200, data: { id: KB_ID, name: 'Handbook' } }, ...routes });
    renderInTheme(<RecentlyDeletedView kbId={KB_ID} />);
    return api;
  }

  it('lists each delete with where it was, who deleted it and when it goes', async () => {
    const api = render({ [`GET ${KB}/${KB_ID}/trash`]: { status: 200, data: page([docs, inDocs]) } });

    const folderRow = await screen.findByRole('row', { name: 'Docs' });
    expect(within(folderRow).getByText('Folder with 3 items')).toBeTruthy();
    expect(within(folderRow).getByText('Handbook')).toBeTruthy();
    expect(within(folderRow).getByText('Ada Admin')).toBeTruthy();
    const fileRow = screen.getByRole('row', { name: 'a.pdf' });
    expect(within(fileRow).getByText('The folder \'Docs\' is deleted too. Restore it first.')).toBeTruthy();
    expect(screen.getByText(/stay here for at least 14 days/)).toBeTruthy();
    expect(screen.getByText('Showing 1–2 of 2')).toBeTruthy();
    expect(api.sent.find((r) => r.url === `${KB}/${KB_ID}/trash`)?.config.params).toEqual({ page: 1, limit: 25 });
  });

  it('restores one item, says so, and reloads the list without it', async () => {
    const api = render({
      [`GET ${KB}/${KB_ID}/trash`]: [{ status: 200, data: page([item('r')]) }, { status: 200, data: page([]) }],
      [`POST ${KB}/record/r/restore`]: { status: 200, data: { success: true, restoredRecords: [{ recordId: 'r' }] } },
    });
    const row = await screen.findByRole('row', { name: 'r.pdf' });

    await act(async () => {
      fireEvent.click(within(row).getByRole('button', { name: /Restore/ }));
    });

    await screen.findByText('Nothing was deleted recently');
    expect(api.count(`POST ${KB}/record/r/restore`)).toBe(1);
    expect(api.count(`GET ${KB}/${KB_ID}/trash`)).toBe(2);
    expect(toasts()).toEqual([{
      variant: 'success',
      title: "Restored 'r.pdf'",
      description: 'Restored items are back where they were, and show up in search again once indexing finishes.',
    }]);
  });

  it('restores a selection in one request, folder first, and names what failed', async () => {
    const api = render({
      [`GET ${KB}/${KB_ID}/trash`]: { status: 200, data: page([inDocs, docs]) },
      [`POST ${KB}/records/restore`]: {
        status: 200,
        data: {
          success: false,
          restoredCount: 4,
          failedCount: 1,
          results: [
            { recordId: 'docs', success: true, restoredRecords: [{ recordId: 'docs' }] },
            { recordId: 'a', success: false, code: 409, reason: 'This item changed while it was being restored, so nothing was restored. Refresh the page and try again.' },
          ],
        },
      },
    });
    await screen.findByRole('row', { name: 'Docs' });

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all on this page' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Restore 2 items/ }));
    });

    await waitFor(() => expect(toasts()).toHaveLength(1));
    expect(api.sent.find((r) => r.url === `${KB}/records/restore`)?.body).toEqual({ recordIds: ['docs', 'a'] });
    expect(toasts()[0].variant).toBe('warning');
    expect(toasts()[0].title).toBe('Restored 1 of 2 items');
    expect(toasts()[0].description).toContain("'a.pdf': This item changed while it was being restored");
  });

  it('says plainly when the list cannot be loaded, and offers to try again', async () => {
    const api = render({
      [`GET ${KB}/${KB_ID}/trash`]: [
        { status: 403, data: { error: { message: "You need edit access to this collection to see and restore its deleted items. Ask the collection's owner for edit access." } } },
        { status: 200, data: page([]) },
      ],
    });

    expect(await screen.findByText(/You need edit access to this collection/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    });
    await screen.findByText('Nothing was deleted recently');
    expect(api.count(`GET ${KB}/${KB_ID}/trash`)).toBe(2);
    expect(toasts()).toEqual([]);
  });

  it('goes back to the collection', async () => {
    render({ [`GET ${KB}/${KB_ID}/trash`]: { status: 200, data: page([]) } });
    await screen.findByText('Nothing was deleted recently');
    fireEvent.click(screen.getByRole('button', { name: 'Back to the collection' }));
    expect(router.push).toHaveBeenCalledWith(`/knowledge-base?nodeType=app&nodeId=${KB_ID}`);
  });
});
