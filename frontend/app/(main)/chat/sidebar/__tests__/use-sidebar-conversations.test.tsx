import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, renderHook, waitFor } from '@testing-library/react';

import { useChatStore } from '@/chat/store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useSidebarConversations } from '../use-sidebar-conversations';

const fetchConversations = vi.fn();
vi.mock('@/chat/api', () => ({
  ChatApi: {
    fetchConversations: (...args: unknown[]) => fetchConversations(...args),
  },
}));

const PAGINATION = {
  page: 1,
  limit: 10,
  totalCount: 0,
  totalPages: 0,
  hasNextPage: false,
  hasPrevPage: false,
};

describe('useSidebarConversations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.getState().reset();
    useServicesHealthStore.setState({ apiServerReachable: true });
    fetchConversations.mockResolvedValue({ conversations: [], pagination: PAGINATION });
  });

  afterEach(() => cleanup());

  it('loads owned and shared recents when the sidebar mounts', async () => {
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(fetchConversations).toHaveBeenCalledTimes(2));
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'owned' });
    expect(fetchConversations).toHaveBeenCalledWith(1, expect.any(Number), { source: 'shared' });
  });

  it('marks the conversation list as failed when it cannot be loaded', async () => {
    fetchConversations.mockRejectedValue(new Error('Request failed with status code 500'));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderHook(() => useSidebarConversations());

    await waitFor(() => expect(useChatStore.getState().conversationsError).toBeTruthy());
    expect(useChatStore.getState().isConversationsLoading).toBe(false);
  });
});
