import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import i18next from 'i18next';

import en from '@/lib/i18n/locales/en-US.json';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string | number>) => i18next.t(key, vars) as string,
  }),
}));

const apiClient = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ apiClient }));
vi.mock('@/config', () => ({
  useAuthStore: Object.assign(
    (selector: (s: { user: { id: string } }) => unknown) => selector({ user: { id: 'me' } }),
    { getState: () => ({ user: { id: 'me' } }) },
  ),
}));
vi.mock('@/lib/store/user-store', () => ({ useUserStore: { getState: () => ({ profile: { userId: 'me' } }) } }));
vi.mock('@/app/components/share/utils', () => ({
  fetchShareUsersPaginated: vi.fn().mockResolvedValue({
    users: [{ id: 'u9', name: 'Dana', email: 'dana@x.io', isInOrg: true }],
    totalCount: 1,
  }),
}));
const listUserTeams = vi.hoisted(() => vi.fn());
vi.mock('@/app/components/share/api', () => ({
  ShareCommonApi: { getAllUsers: vi.fn().mockResolvedValue([]), listUserTeams },
}));
vi.mock('@/lib/store/toast-store', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/app/components/team', () => ({ CreateTeamForm: () => null }));
vi.mock('@/app/(main)/workspace/components/avatar-cell', () => ({
  AvatarCell: ({ name }: { name: string }) => <span>{name}</span>,
}));
vi.mock('@/app/(main)/workspace/users/api', () => ({ UsersApi: { getUsersByIds: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/app/(main)/agents/api', () => ({ AgentsApi: { fetchAgentConversation: vi.fn() } }));

import { ShareSidebar } from '../share-sidebar';
import { createChatShareAdapter } from '@/app/(main)/chat/share-adapter';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';

const OWNER_VIEW = {
  owner: { userId: 'me', displayName: 'Me' },
  collaborators: [
    { principalType: 'user', principalId: 'u2', displayName: 'Bob', accessLevel: 'write', state: 'active' },
    { principalType: 'user', principalId: 'u3', displayName: 'Cleo', accessLevel: 'read', state: 'active' },
    { principalType: 'team', principalId: 't-sales', displayName: 'Sales', accessLevel: 'read', state: 'active' },
    { principalType: 'team', principalId: 't-other', displayName: 'A team', accessLevel: 'read', state: 'active' },
    { principalType: 'user', principalId: 'u4', displayName: 'x', accessLevel: 'read', state: 'former_member' },
    { principalType: 'team', principalId: 't-gone', displayName: 'x', accessLevel: 'read', state: 'deleted_team' },
  ],
  collaboratorCount: 6,
  settings: { editorsCanInvite: false, ownerContentShared: true },
};

const INVITER_VIEW = {
  ...OWNER_VIEW,
  owner: { userId: 'owner-1', displayName: 'Olga' },
  collaborators: [
    { principalType: 'user', principalId: 'me', displayName: 'Me', accessLevel: 'write', state: 'active' },
    { principalType: 'user', principalId: 'u2', displayName: 'Bob', accessLevel: 'write', state: 'active' },
  ],
  collaboratorCount: 2,
};

const READER_SUMMARY = { owner: { userId: 'owner-1', displayName: 'Olga' }, collaboratorCount: 7, myAccess: 'read' };

function renderDrawer(options?: { agentId?: string }) {
  const adapter = createChatShareAdapter('c1', { ...options, collaborative: true });
  const onShareSuccess = vi.fn();
  render(
    <Theme>
      <ShareSidebar open onOpenChange={() => {}} adapter={adapter} onShareSuccess={onShareSuccess} />
    </Theme>,
  );
  return { adapter, onShareSuccess };
}

async function selectUser(name: string) {
  fireEvent.click(await screen.findByRole('checkbox', { name }));
}

beforeEach(async () => {
  vi.clearAllMocks();
  await i18next.init({ lng: 'en', fallbackLng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
  listUserTeams.mockResolvedValue([
    { id: 't1', name: 'Eng', memberCount: 8 },
    { id: 'all_org1', name: 'Everyone', memberCount: 12 },
    { id: 't-big', name: 'Company', memberCount: 80 },
  ]);
  apiClient.put.mockResolvedValue({ data: OWNER_VIEW });
  apiClient.get.mockResolvedValue({ data: OWNER_VIEW });
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('owner view', () => {
  it('lists users and teams by name, "A team" as the server sent it, and Former member / Deleted team', async () => {
    renderDrawer();
    expect(await screen.findByText('Bob')).toBeTruthy();
    expect(screen.getByText('Sales')).toBeTruthy();
    expect(screen.getByText('A team')).toBeTruthy();
    expect(screen.getAllByText(en.chat.collab.share.formerMember).length).toBeGreaterThan(0);
    expect(screen.getAllByText(en.chat.collab.share.deletedTeam).length).toBeGreaterThan(0);
    expect(screen.getByRole('dialog', { name: en.chat.collab.share.title })).toBeTruthy();
    expect(screen.getByRole('note').textContent).toBe(en.chat.collab.share.notice);
  });

  it('offers Can continue and Can view, never OWNER, and Make owner only on a direct write user', async () => {
    renderDrawer();
    await screen.findByText('Bob');

    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    expect(screen.getAllByRole('menuitemradio').map((el) => el.textContent)).toEqual([
      expect.stringContaining(en.chat.collab.share.roleWrite),
      expect.stringContaining(en.chat.collab.share.roleRead),
    ]);
    expect(screen.queryByText(en.shareSidebar.roles.owner.label)).toBeNull();
    expect(screen.getByRole('menuitem', { name: en.chat.collab.share.makeOwner })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
  });

  it('does not offer Make owner on a reader or a team', async () => {
    renderDrawer();
    await screen.findByText('Cleo');
    const readerTriggers = screen.getAllByRole('button', { name: new RegExp(en.chat.collab.share.roleRead) });
    for (const trigger of readerTriggers) {
      fireEvent.click(trigger);
      expect(screen.queryByRole('menuitem', { name: en.chat.collab.share.makeOwner })).toBeNull();
      fireEvent.click(trigger);
    }
  });

  it('changes a level with PUT', async () => {
    renderDrawer();
    await screen.findByText('Cleo');
    // teams are listed first: Sales, A team, then Bob (write) and Cleo
    const readerTriggers = screen.getAllByRole('button', { name: new RegExp(en.chat.collab.share.roleRead) });
    fireEvent.click(readerTriggers[2]);
    fireEvent.click(screen.getByRole('menuitemradio', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(apiClient.put.mock.calls[0][1]).toEqual({
      collaborators: [{ principalType: 'user', principalId: 'u3', accessLevel: 'write' }],
    });
  });

  it('removes a collaborator with DELETE and its type', async () => {
    apiClient.delete.mockResolvedValue({ data: OWNER_VIEW });
    renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    fireEvent.click(screen.getByRole('menuitem', { name: en.action.remove }));
    await waitFor(() => expect(apiClient.delete).toHaveBeenCalled());
    expect(apiClient.delete.mock.calls[0][0]).toBe('/api/v1/conversations/c1/collaborators/u2');
    expect(apiClient.delete.mock.calls[0][1].params).toEqual({ principalType: 'user' });
  });
});

describe('selection cap (the backend PUT takes at most 50)', () => {
  const manyUsers = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ id: `m${i}`, name: `Person ${i}`, email: `p${i}@x.io`, isInOrg: true }));

  afterEach(() => {
    vi.mocked(fetchShareUsersPaginated).mockResolvedValue({
      users: [{ id: 'u9', name: 'Dana', email: 'dana@x.io', isInOrg: true }],
      totalCount: 1,
    } as never);
  });

  async function selectPeople(count: number) {
    for (let i = 0; i < count; i++) {
      fireEvent.click(await screen.findByRole('checkbox', { name: `Person ${i}` }));
    }
  }

  it('50 selected: Share is enabled and sends one PUT of 50', async () => {
    vi.mocked(fetchShareUsersPaginated).mockResolvedValue({ users: manyUsers(51), totalCount: 51 } as never);
    renderDrawer();
    await screen.findByText('Bob');
    await selectPeople(50);
    expect(screen.queryByTestId('share-limit-message')).toBeNull();
    const share = screen.getByRole('button', { name: en.action.share }) as HTMLButtonElement;
    expect(share.disabled).toBe(false);
    fireEvent.click(share);
    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(apiClient.put.mock.calls[0][1].collaborators).toHaveLength(50);
  });

  it('51 selected: a clear message, Share disabled, nothing sent; removing one re-enables it', async () => {
    vi.mocked(fetchShareUsersPaginated).mockResolvedValue({ users: manyUsers(51), totalCount: 51 } as never);
    renderDrawer();
    await screen.findByText('Bob');
    await selectPeople(51);
    const message = screen.getByTestId('share-limit-message');
    expect(message.getAttribute('role')).toBe('alert');
    expect(message.textContent).toBe('You can add up to 50 people or teams at a time. Remove 1 to continue.');
    const share = screen.getByRole('button', { name: en.action.share }) as HTMLButtonElement;
    expect(share.disabled).toBe(true);
    fireEvent.click(share);
    expect(apiClient.put).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Remove p50@x.io' }));
    expect(screen.queryByTestId('share-limit-message')).toBeNull();
    expect((screen.getByRole('button', { name: en.action.share }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('adding people, teams and a note', () => {
  it('PUTs a user and a team at their levels, with the note, to the chat route', async () => {
    const { onShareSuccess } = renderDrawer();
    await screen.findByText('Bob');
    await selectUser('Dana');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Eng' }));

    const note = screen.getByLabelText(en.chat.collab.share.noteLabel) as HTMLTextAreaElement;
    expect(note.maxLength).toBe(500);
    fireEvent.change(note, { target: { value: 'Out until the 14th' } });
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    const [url, body] = apiClient.put.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/c1/collaborators');
    expect(body).toEqual({
      collaborators: [
        { principalType: 'user', principalId: 'u9', accessLevel: 'read' },
        { principalType: 'team', principalId: 't1', accessLevel: 'read' },
      ],
      note: 'Out until the 14th',
    });
    await waitFor(() => expect(onShareSuccess).toHaveBeenCalled());
  });

  it('uses the agent route for an agent chat', async () => {
    renderDrawer({ agentId: 'agent 7' });
    await screen.findByText('Bob');
    await selectUser('Dana');
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(apiClient.put.mock.calls[0][0]).toBe('/api/v1/agents/agent%207/conversations/c1/collaborators');
  });

  it('shows no note box until someone is selected', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    expect(screen.queryByLabelText(en.chat.collab.share.noteLabel)).toBeNull();
  });
});

describe('org-wide and large team confirmation', () => {
  it('asks before sharing with the whole organization; Cancel sends nothing, Confirm sends confirmOrgWide', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Everyone' }));
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));

    let confirm = await screen.findByRole('alertdialog');
    expect(within(confirm).getByText(en.chat.collab.share.orgWideTitle)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: en.common.cancel }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(apiClient.put).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: en.action.share }));
    confirm = await screen.findByRole('alertdialog');
    fireEvent.click(within(confirm).getByRole('button', { name: en.chat.collab.share.orgWideConfirm }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(apiClient.put.mock.calls[0][1]).toEqual({
      collaborators: [{ principalType: 'team', principalId: 'all_org1', accessLevel: 'read' }],
      confirmOrgWide: true,
    });
  });

  it('confirms a team over 50 members but does not send confirmOrgWide', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Company' }));
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));
    const confirm = await screen.findByRole('alertdialog');
    expect(within(confirm).getByText(en.chat.collab.share.largeTeamTitle)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole('button', { name: en.chat.collab.share.largeTeamConfirm }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(apiClient.put.mock.calls[0][1]).not.toHaveProperty('confirmOrgWide');
  });

  it('shares a small team without a prompt', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Eng' }));
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));
    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});

describe('transfer ownership', () => {
  it('confirms, then POSTs the direct write user as the new owner', async () => {
    apiClient.post.mockResolvedValue({ data: { ...INVITER_VIEW } });
    const { onShareSuccess } = renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    fireEvent.click(screen.getByRole('menuitem', { name: en.chat.collab.share.makeOwner }));

    const confirm = await screen.findByRole('alertdialog');
    expect(within(confirm).getByText('Make Bob the owner?')).toBeTruthy();
    expect(apiClient.post).not.toHaveBeenCalled();
    apiClient.get.mockResolvedValue({ data: INVITER_VIEW });
    fireEvent.click(within(confirm).getByRole('button', { name: en.chat.collab.share.makeOwner }));

    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(1));
    expect(apiClient.post.mock.calls[0][0]).toBe('/api/v1/conversations/c1/transfer-ownership');
    expect(apiClient.post.mock.calls[0][1]).toEqual({ newOwnerUserId: 'u2' });
    await waitFor(() => expect(onShareSuccess).toHaveBeenCalled());
    // the drawer re-reads the view: this person is no longer the owner, so settings are gone
    await waitFor(() => expect(screen.queryByRole('switch')).toBeNull());
  });
});

describe('settings', () => {
  it('shows both switches to the owner and PATCHes a toggle', async () => {
    apiClient.patch.mockResolvedValue({ data: OWNER_VIEW });
    renderDrawer();
    const invite = await screen.findByRole('switch', { name: en.chat.collab.share.settingEditorsCanInvite });
    expect(invite.getAttribute('aria-checked')).toBe('false');
    expect(screen.getByRole('switch', { name: en.chat.collab.share.settingOwnerContentShared }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(invite);
    await waitFor(() => expect(apiClient.patch).toHaveBeenCalledTimes(1));
    expect(apiClient.patch.mock.calls[0][0]).toBe('/api/v1/conversations/c1/collaboration-settings');
    expect(apiClient.patch.mock.calls[0][1]).toEqual({ editorsCanInvite: true });
    expect(invite.getAttribute('aria-checked')).toBe('true');
  });

  it('reverts the switch and shows the coded error when the update fails', async () => {
    apiClient.patch.mockRejectedValue({ code: 'CONVERSATION_OWNER_ONLY', statusCode: 403, message: 'raw' });
    renderDrawer();
    const invite = await screen.findByRole('switch', { name: en.chat.collab.share.settingEditorsCanInvite });
    fireEvent.click(invite);
    expect((await screen.findByRole('alert')).textContent).toBe(en.chat.collab.errors.CONVERSATION_OWNER_ONLY);
    expect(invite.getAttribute('aria-checked')).toBe('false');
  });
});

describe('error codes on share', () => {
  const cases: Array<[string, Record<string, unknown> | undefined, string]> = [
    ['COLLABORATOR_LIMIT', { max: 200 }, 'This chat can have at most 200 people and teams.'],
    ['COLLABORATOR_LIMIT', undefined, en.chat.collab.errors.COLLABORATOR_LIMIT],
    ['INVALID_PRINCIPAL', undefined, en.chat.collab.errors.INVALID_PRINCIPAL],
    ['RATE_LIMITED', { retryAfter: 12 }, 'Too many changes. Try again in 12 seconds.'],
    ['RATE_LIMITED', undefined, en.chat.collab.errors.RATE_LIMITED],
    ['ORG_WIDE_CONFIRMATION_REQUIRED', undefined, en.chat.collab.errors.ORG_WIDE_CONFIRMATION_REQUIRED],
    ['CONVERSATION_OWNER_ONLY', undefined, en.chat.collab.errors.CONVERSATION_OWNER_ONLY],
    ['TEAM_RESOLUTION_UNAVAILABLE', undefined, en.chat.collab.errors.TEAM_RESOLUTION_UNAVAILABLE],
    ['SOMETHING_NEW', undefined, en.chat.collab.errors.GENERIC],
  ];

  it.each(cases)('%s %j is shown inline and keeps the selection', async (code, details, expected) => {
    apiClient.put.mockRejectedValue({ code, details, statusCode: 400, message: 'raw server text' });
    renderDrawer();
    await screen.findByText('Bob');
    await selectUser('Dana');
    fireEvent.click(screen.getByRole('button', { name: en.action.share }));
    expect((await screen.findByRole('alert')).textContent).toBe(expected);
    expect(screen.getByLabelText(en.chat.collab.share.noteLabel)).toBeTruthy();
  });
});

describe('inviting editor', () => {
  beforeEach(() => {
    apiClient.get.mockResolvedValue({ data: INVITER_VIEW });
  });

  it('can add but not change, remove, transfer or configure', async () => {
    renderDrawer();
    expect(await screen.findByText('Bob')).toBeTruthy();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.queryByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) })).toBeNull();
    expect(screen.getAllByText(en.chat.collab.share.roleWrite).length).toBeGreaterThan(0);
    await selectUser('Dana');
    expect(screen.getByRole('button', { name: en.action.share })).toBeTruthy();
  });

  it('offers at most Can continue for what it adds', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    await selectUser('Dana');
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleRead) }));
    expect(screen.getAllByRole('menuitemradio').length).toBe(2);
    expect(screen.queryByText(en.shareSidebar.roles.owner.label)).toBeNull();
  });
});

describe('drawer size', () => {
  const dialog = () => screen.getByRole('dialog', { name: en.chat.collab.share.title });

  it('is a floating 37.5rem panel on desktop', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    expect(dialog().style.width).toBe('37.5rem');
    expect(dialog().style.top).toBe('10px');
  });

  it('fills the screen on a phone', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: true, media: query, addEventListener() {}, removeEventListener() {} }));
    renderDrawer();
    await screen.findByText('Bob');
    expect(dialog().style.width).toBe('100%');
    expect(dialog().style.top).toBe('0px');
    expect(dialog().style.left).toBe('0px');
    expect(dialog().style.right).toBe('0px');
  });
});

describe('suggested members', () => {
  it('leaves out disabled users, service accounts and the owner, so only people the server accepts are offered', async () => {
    const { adapter } = renderDrawer();
    await screen.findByText('Bob');
    await adapter.getSharingUsersPaginated!({ page: 1, limit: 10 });
    const exclude = vi.mocked(fetchShareUsersPaginated).mock.lastCall![1]!.exclude!;
    const user = (userId: string, extra = {}) => ({ id: userId, userId, hasLoggedIn: true, isActive: true, ...extra });
    expect(exclude(user('u9') as never)).toBe(false);
    expect(exclude(user('u8', { isDisabled: true }) as never)).toBe(true);
    expect(exclude(user('u7', { kind: 'service' }) as never)).toBe(true);
    expect(exclude(user('me') as never)).toBe(true);
  });
});

describe('reader summary', () => {
  it('shows the count and own access, never other members, and no way to share', async () => {
    apiClient.get.mockResolvedValue({ data: READER_SUMMARY });
    renderDrawer();
    expect(await screen.findByText('Owner: Olga')).toBeTruthy();
    expect(screen.getByText('People and teams with access: 7')).toBeTruthy();
    expect(screen.getByText('Your access: Can view')).toBeTruthy();
    expect(screen.queryByText('Bob')).toBeNull();
    expect(screen.queryByRole('button', { name: en.action.share })).toBeNull();
    expect(screen.queryByLabelText(en.chat.collab.share.searchLabel)).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
    // Nothing to cancel in a read-only summary: the footer button closes it.
    expect(screen.queryByRole('button', { name: en.action.cancel })).toBeNull();
    expect(screen.getAllByRole('button', { name: en.common.close }).length).toBeGreaterThan(0);
  });

  it('lists no suggested members or teams and no Create a New Team, since nothing can be shared', async () => {
    apiClient.get.mockResolvedValue({ data: READER_SUMMARY });
    renderDrawer();
    await screen.findByText('Owner: Olga');
    await waitFor(() => expect(listUserTeams).toHaveBeenCalled());
    expect(screen.queryByText(en.shareSidebar.suggestedMembers)).toBeNull();
    expect(screen.queryByText(en.shareSidebar.suggestedTeams)).toBeNull();
    expect(screen.queryByText('Dana')).toBeNull();
    expect(screen.queryByText('Eng')).toBeNull();
    expect(screen.queryByRole('button', { name: en.shareSidebar.createNewTeam })).toBeNull();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });
});

describe('accessibility', () => {
  it('labels the search input and close button, and keeps focus inside the dialog', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByLabelText(en.chat.collab.share.searchLabel)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: en.common.close })).toBeTruthy();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('selects a row with Enter', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    const dana = await screen.findByRole('checkbox', { name: 'Dana' });
    expect(dana.getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(dana, { key: 'Enter' });
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Dana' })).toBeNull());
    expect(screen.getByLabelText(en.chat.collab.share.noteLabel)).toBeTruthy();
  });

  it('changes a level from the menu with the keyboard', async () => {
    renderDrawer();
    await screen.findByText('Bob');
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    const item = screen.getByRole('menuitemradio', { name: new RegExp(en.chat.collab.share.roleRead) });
    expect(item.getAttribute('tabindex')).toBe('0');
    fireEvent.keyDown(item, { key: ' ' });
    await waitFor(() => expect(apiClient.put).toHaveBeenCalled());
    expect(apiClient.put.mock.calls[0][1].collaborators[0]).toMatchObject({ principalId: 'u2', accessLevel: 'read' });
  });
});
