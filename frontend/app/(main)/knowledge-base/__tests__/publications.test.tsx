/**
 * U4 KP-1..KP-6: a collection's knowledge publications (P2.13): list, publish
 * to an audience for answers only or in full until an expiry, revoke; owners
 * and admins only, as the query service decides.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { apiClient } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { nodeRefusal } from '@/workspace/harness-ui/__tests__/fake-refusal';
import { canPublishCollection } from '../publications/api';
import { expiryFromDate, PublicationsPanel } from '../publications/publications-panel';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});
vi.mock('@/workspace/approvals/directory', () => ({
  ApprovalsDirectory: { names: vi.fn(async () => ({ 'u-bo': 'Bo Reader' })) },
}));
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { listUserTeams: vi.fn(async () => [{ id: 't-sales', name: 'Sales', memberCount: 4 }]) },
}));
vi.mock('@/app/components/share/utils', () => ({
  fetchShareUsersPaginated: vi.fn(async () => ({
    users: [{ id: 'u-bo', name: 'Bo Reader', email: 'bo@example.com', isInOrg: true }],
    totalCount: 1,
  })),
}));

const get = vi.mocked(apiClient.get);
const post = vi.mocked(apiClient.post);
const del = vi.mocked(apiClient.delete);

const publication = (over: Record<string, unknown> = {}) => ({
  id: 'pub-1',
  containerKind: 'kb',
  containerId: 'kb-eng',
  audience: { userIds: ['u-bo'], teamIds: [], org: false },
  mode: 'answer_only',
  createdBy: 'u-owner',
  createdAt: Date.UTC(2026, 9, 1),
  expiresAt: null,
  revokedAt: null,
  revokedBy: null,
  ...over,
});

let list: Array<Record<string, unknown>>;
let listFailure: unknown;
let createFailure: unknown;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  list = [publication(), publication({ id: 'pub-2', audience: { userIds: [], teamIds: ['t-sales'], org: false }, mode: 'full', revokedAt: Date.UTC(2026, 9, 2) })];
  listFailure = null;
  createFailure = null;
  get.mockImplementation(async () => {
    if (listFailure) throw listFailure;
    return { data: { publications: list } };
  });
  post.mockImplementation(async (_url: string, body?: unknown) => {
    if (createFailure) throw createFailure;
    const created = publication({ id: 'pub-new', ...(body as Record<string, unknown>) });
    list = [...list, created];
    return { data: created };
  });
  del.mockImplementation(async (url: string) => {
    const id = url.split('/').pop();
    list = list.map((p) => (p.id === id ? { ...p, revokedAt: Date.now(), revokedBy: 'u-owner' } : p));
    return { data: list.find((p) => p.id === id) };
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderPanel = () =>
  render(
    <Theme>
      <PublicationsPanel open onOpenChange={vi.fn()} kbId="kb-eng" kbName="Engineering" />
    </Theme>,
  );

const items = async () => screen.findAllByTestId('publication');

describe('PublicationsPanel', () => {
  it('KP-1: the header offers Publish only with the flag on, on a root collection, to an owner or admin', () => {
    const base = { flagOn: true, isRootCollection: true, isOwner: true, isAdmin: false };
    expect(canPublishCollection(base)).toBe(true);
    expect(canPublishCollection({ ...base, flagOn: false })).toBe(false);
    expect(canPublishCollection({ ...base, isRootCollection: false })).toBe(false);
    expect(canPublishCollection({ ...base, isOwner: false })).toBe(false);
    expect(canPublishCollection({ ...base, isOwner: false, isAdmin: true })).toBe(true);
    expect(canPublishCollection({ ...base, isOwner: false, isAdmin: null })).toBe(false);
  });

  it('KP-2: the list names the audience, the mode, the expiry and a revoked publication', async () => {
    renderPanel();
    const [first, second] = await items();
    expect(get).toHaveBeenCalledWith('/api/v1/agent-harness/publications', expect.objectContaining({ params: { containerKind: 'kb', containerId: 'kb-eng' } }));
    await waitFor(() => expect(within(first).getByText('Bo Reader')).toBeTruthy());
    expect(within(first).getByText('Answers only')).toBeTruthy();
    expect(within(first).getByText('No expiry')).toBeTruthy();
    expect(within(first).getByRole('button', { name: 'Revoke' })).toBeTruthy();
    expect(within(second).getByText('Sales')).toBeTruthy();
    expect(within(second).getByText('Revoked')).toBeTruthy();
    expect(within(second).queryByRole('button', { name: 'Revoke' })).toBeNull();
    cleanup();

    list = [];
    renderPanel();
    expect(await screen.findByText('This collection is not published to anyone.')).toBeTruthy();
  });

  it('KP-3: publishing sends the audience, the mode and the expiry; an empty audience cannot be published', async () => {
    renderPanel();
    await items();
    const form = screen.getByRole('group', { name: 'Publish to more people' });
    const submit = within(form).getByRole('button', { name: 'Publish' });
    expect(submit.hasAttribute('disabled')).toBe(true);

    fireEvent.change(within(form).getByLabelText('People'), { target: { value: 'bo' } });
    fireEvent.click(await within(form).findByRole('button', { name: /Bo Reader/ }));
    fireEvent.click(await within(form).findByRole('checkbox', { name: 'Sales' }));
    fireEvent.click(within(form).getByRole('radio', { name: /Full access/ }));
    fireEvent.change(within(form).getByLabelText('Expires at the end of (optional)'), { target: { value: '2026-12-31' } });
    expect(submit.hasAttribute('disabled')).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agent-harness/publications',
      {
        containerKind: 'kb',
        containerId: 'kb-eng',
        audience: { userIds: ['u-bo'], teamIds: ['t-sales'], org: false },
        mode: 'full',
        expiresAt: expiryFromDate('2026-12-31'),
      },
      { suppressErrorToast: true },
    );
    await waitFor(async () => expect(await items()).toHaveLength(3));
  });

  it('KP-4: revoke asks first and the row then reads "Revoked"', async () => {
    renderPanel();
    const [first] = await items();
    fireEvent.click(within(first).getByRole('button', { name: 'Revoke' }));
    let dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(within(first).getByRole('button', { name: 'Revoke' }));
    dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith('/api/v1/agent-harness/publications/pub-1', { suppressErrorToast: true }));
    await waitFor(async () => expect(within((await items())[0]).getByText('Revoked')).toBeTruthy());
  });

  it.each([
    ['PUBLICATION_INVALID', 400, 'expiresAt must be in the future', /Check the publication\. expiresAt must be in the future/],
    ['PUBLICATION_NOT_FOUND', 404, 'x', /no longer exists/],
    ['PUBLICATIONS_DISABLED', 404, 'x', /turned off for your organization/],
    ['PUBLICATIONS_UNAVAILABLE', 503, 'x', /can't be reached right now/],
    ['PUBLICATION_FORBIDDEN', 403, 'x', /Only owners of this collection and organization admins/],
  ])('KP-5: %s on publish is shown in words', async (code, status, message, shown) => {
    createFailure = nodeRefusal(status, code, message);
    renderPanel();
    await items();
    const form = screen.getByRole('group', { name: 'Publish to more people' });
    fireEvent.click(within(form).getByRole('checkbox', { name: 'Everyone in the organization' }));
    fireEvent.click(within(form).getByRole('button', { name: 'Publish' }));
    expect((await within(form).findByRole('alert')).textContent).toMatch(shown);
  });

  it('KP-6: a reader who is neither owner nor admin is told so and gets no form', async () => {
    listFailure = nodeRefusal(403, 'PUBLICATION_FORBIDDEN');
    renderPanel();
    expect((await screen.findByRole('alert')).textContent).toBe('Only owners of this collection and organization admins can publish it.');
    expect(screen.queryByRole('group', { name: 'Publish to more people' })).toBeNull();
  });
});
