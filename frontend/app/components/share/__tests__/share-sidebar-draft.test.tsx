import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
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
import { createDraftShareAdapter } from '@/chat/draft-share-adapter';
import { useDraftShareStore } from '@/chat/draft-share-store';
import { toast } from '@/lib/store/toast-store';
import { fetchShareUsersPaginated } from '@/app/components/share/utils';


function renderDraft(props: { initialSearch?: string } = {}) {
  const adapter = createDraftShareAdapter();
  const onShareSuccess = vi.fn();
  render(
    <Theme>
      <ShareSidebar draft open onOpenChange={() => {}} adapter={adapter} onShareSuccess={onShareSuccess} {...props} />
    </Theme>,
  );
  return { onShareSuccess };
}

beforeEach(async () => {
  vi.clearAllMocks();
  useDraftShareStore.getState().clear();
  await i18next.init({ lng: 'en', fallbackLng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
  listUserTeams.mockResolvedValue([{ id: 't1', name: 'Eng', memberCount: 8 }]);
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the drawer for a chat that does not exist yet', () => {
  it('Add stores the pick in the draft and calls no API', async () => {
    const { onShareSuccess } = renderDraft();
    expect(screen.getByRole('note').textContent).toBe(en.chat.collab.share.draftNotice);
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Dana' }));
    fireEvent.click(screen.getByRole('button', { name: en.chat.collab.share.draftAdd }));
    await waitFor(() => expect(useDraftShareStore.getState().principals).toEqual([
      { type: 'user', id: 'u9', name: 'Dana', email: 'dana@x.io', level: 'read' },
    ]));
    expect(apiClient.put).not.toHaveBeenCalled();
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(onShareSuccess).toHaveBeenCalled();
    expect(await screen.findAllByText('Dana')).not.toHaveLength(0);
  });

  it('keeps the chosen level and the optional message for the first send', async () => {
    renderDraft();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Dana' }));
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleRead) }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    fireEvent.change(screen.getByLabelText(en.chat.collab.share.noteLabel), { target: { value: 'welcome aboard' } });
    fireEvent.click(screen.getByRole('button', { name: en.chat.collab.share.draftAdd }));
    await waitFor(() => expect(useDraftShareStore.getState().principals[0]?.level).toBe('write'));
    expect(useDraftShareStore.getState().message).toBe('welcome aboard');
  });

  it('a drafted person can be removed from the list in the drawer', async () => {
    useDraftShareStore.getState().add([{ type: 'user', id: 'u9', name: 'Dana', level: 'write' }]);
    renderDraft();
    await screen.findByText('Dana');
    fireEvent.click(screen.getByRole('button', { name: new RegExp(en.chat.collab.share.roleWrite) }));
    fireEvent.click(screen.getByRole('menuitem', { name: en.action.remove }));
    await waitFor(() => expect(useDraftShareStore.getState().principals).toEqual([]));
  });

  it('opens with the typed query already in the people search', async () => {
    renderDraft({ initialSearch: 'dan' });
    await waitFor(() =>
      expect(fetchShareUsersPaginated).toHaveBeenCalledWith(expect.objectContaining({ search: 'dan' }), expect.anything()),
    );
  });
});
