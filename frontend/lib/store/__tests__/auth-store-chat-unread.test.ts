/**
 * The unread-chat markers are tied to the session: a deliberate sign-out wipes
 * them, a new sign-in lets them be saved again, and an expired session keeps them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

vi.mock('@/lib/electron', () => ({ isElectron: () => true }));
vi.mock('@/lib/electron/api-base-url-storage', () => ({
  clearElectronLogoutServerState: vi.fn(),
  persistElectronServerUrlOnLogin: vi.fn(),
}));

const backing = installMemoryStorage();

const { useAuthStore, logoutFromWorkspaceMenu } = await import('../auth-store');
const { rememberUnreadConversation, loadUnreadConversationIds } = await import('@/lib/storage/chat-unread');

const saved = (userId: string) => [...loadUnreadConversationIds(userId)];

beforeEach(() => {
  backing.clear();
  useAuthStore.getState().setTokens('access', 'refresh');
});

describe('unread chats and the session', () => {
  it('are wiped when the user signs out from the workspace menu', () => {
    rememberUnreadConversation('user-1', 'conv-1');

    logoutFromWorkspaceMenu();

    expect(saved('user-1')).toEqual([]);
    expect(backing.size).toBe(0);
  });

  it('cannot be saved by a run that finishes after the sign-out', () => {
    logoutFromWorkspaceMenu();

    rememberUnreadConversation('user-1', 'conv-late');

    expect(saved('user-1')).toEqual([]);
  });

  it('are saved again after the next sign-in', () => {
    logoutFromWorkspaceMenu();

    useAuthStore.getState().setTokens('access-2', 'refresh-2');
    rememberUnreadConversation('user-1', 'conv-1');

    expect(saved('user-1')).toEqual(['conv-1']);
  });

  it('are kept when the session only expires, so nothing is lost', () => {
    rememberUnreadConversation('user-1', 'conv-1');

    useAuthStore.getState().logout();

    expect(saved('user-1')).toEqual(['conv-1']);
  });
});
