/**
 * Everything kept between screens for the signed-in person is dropped at
 * session end. Signing out on the desktop app does not reload the page, so a
 * store or module left out of this would show its data to the next person, or
 * keep the next person waiting on a request of the last session that never
 * settles. Only the HTTP client is faked.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const http = vi.hoisted(() => {
  const never = () => new Promise<never>(() => {});
  const client = { get: vi.fn(never), post: vi.fn(never), put: vi.fn(never), patch: vi.fn(never), delete: vi.fn(never) };
  return { client };
});
vi.mock('@/lib/api/axios-instance', () => ({
  apiClient: http.client,
  default: http.client,
  isRefreshInProgress: () => false,
}));

import { clearSessionCache } from '../session-cache';
import { useUsersStore } from '@/app/(main)/workspace/users/store';
import { useGroupsStore } from '@/app/(main)/workspace/groups/store';
import { useTeamsStore } from '@/app/(main)/workspace/teams/store';
import { useGeneralStore } from '@/app/(main)/workspace/general/store';
import { useProfileStore } from '@/app/(main)/workspace/profile/store';
import { useConnectorsStore } from '@/app/(main)/workspace/connectors/store';
import { useAIModelsStore } from '@/app/(main)/workspace/ai-models/store';
import { useBotsStore } from '@/app/(main)/workspace/bots/store';
import { useSkillsStore } from '@/app/(main)/workspace/skills/personal/store';
import { useLabsStore } from '@/app/(main)/workspace/labs/store';
import { useOnboardingStore } from '@/app/(main)/onboarding/store';
import { useArchivedChatsStore } from '@/app/(main)/workspace/archived-chats/store';
import { useArtifactsStore } from '@/app/(main)/artifacts/store';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useOnboardingTourStore } from '@/app/(main)/components/tours/onboarding/store';
import { useInlineCitationPopoverStore } from '@/app/(main)/chat/components/message-area/response-tabs/citations/citation-popover-store';
import { useUploadStore } from '@/lib/store/upload-store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { useOrgStore } from '@/lib/store/org-store';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { useLanguageStore } from '@/lib/store/language-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useNotificationPanelWidthStore } from '@/app/(main)/notifications/panel-width-store';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { toast, useToastStore } from '@/lib/store/toast-store';
import { fetchFrontendPublicUrl } from '../frontend-public-url';
import { fetchSecretRevealAvailable } from '@/lib/hooks/use-secret-reveal-available';
import { useUploadLimits, DEFAULT_MAX_FILE_SIZE_BYTES } from '@/lib/hooks/use-upload-limits';
import { fetchOrgWithLogo } from '@/app/(main)/chat/api';
import { fetchModelsForContext } from '@/app/(main)/chat/utils/fetch-models-for-context';
import { ASSISTANT_CTX } from '@/chat/store';

interface AnyStore {
  getState(): object;
  getInitialState(): object;
  setState(partial: object): void;
}

const userDataStores: Array<[string, AnyStore]> = [
  ['users', useUsersStore],
  ['groups', useGroupsStore],
  ['teams', useTeamsStore],
  ['general (org settings)', useGeneralStore],
  ['profile form', useProfileStore],
  ['connectors', useConnectorsStore],
  ['AI models', useAIModelsStore],
  ['bots', useBotsStore],
  ['personal skills', useSkillsStore],
  ['labs', useLabsStore],
  ['org onboarding wizard', useOnboardingStore],
  ['archived chats', useArchivedChatsStore],
  ['artifacts', useArtifactsStore],
  ['notifications', useNotificationStore],
  ['onboarding tour', useOnboardingTourStore],
  ['citation popover', useInlineCitationPopoverStore],
  ['upload tray', useUploadStore],
  ['pending chat hand-off', usePendingChatStore],
  ['org', useOrgStore],
  ['feature flags', useFeatureFlagsStore],
];

beforeEach(() => {
  http.client.get.mockClear();
});

describe('stores at session end', () => {
  it.each(userDataStores)('the %s store is back to its initial state', (_name, store) => {
    store.setState({ leftoverFromLastPerson: true });

    clearSessionCache();

    expect(store.getState()).toEqual(store.getInitialState());
    expect('leftoverFromLastPerson' in store.getState()).toBe(false);
  });

  it('keeps device preferences', () => {
    useLanguageStore.setState({ leftoverFromLastPerson: true } as never);
    useSidebarWidthStore.setState({ leftoverFromLastPerson: true } as never);
    useNotificationPanelWidthStore.setState({ leftoverFromLastPerson: true } as never);

    clearSessionCache();

    expect('leftoverFromLastPerson' in useLanguageStore.getState()).toBe(true);
    expect('leftoverFromLastPerson' in useSidebarWidthStore.getState()).toBe(true);
    expect('leftoverFromLastPerson' in useNotificationPanelWidthStore.getState()).toBe(true);
  });

  it('forgets a chat hand-off, so the next person sends nothing', () => {
    usePendingChatStore.getState().setPending({ message: 'about Q3', pageContext: {}, referrerPage: '/knowledge-base' });

    clearSessionCache();

    expect(usePendingChatStore.getState().consumePending()).toBeNull();
  });

  it('dismisses loading toasts, whose work never finishes, and keeps the others', () => {
    useToastStore.setState({ toasts: [] });
    toast.loading('Deleting 3 items...');
    toast.success('Password updated');

    clearSessionCache();

    const left = useToastStore.getState().toasts.filter((t) => !t.isExiting);
    expect(left.map((t) => t.variant)).toEqual(['success']);
  });
});

// A request of the last session never settles, so a call that joined it would wait forever.
describe('shared in-flight requests at session end', () => {
  const callsTo = (fragment: string) => http.client.get.mock.calls.filter(([url]) => String(url).includes(fragment)).length;

  it.each([
    ['the frontend public URL', 'frontend', () => void fetchFrontendPublicUrl()],
    ['the secret reveal check', 'secretReveal', () => void fetchSecretRevealAvailable()],
    ['the feature flags', 'feature-flags', () => void useFeatureFlagsStore.getState().fetchFlags()],
    ['the server retry', '/api/v1/health', () => void useServicesHealthStore.getState().retryServerConnection()],
    ['the org and logo', '/api/v1/org', () => void fetchOrgWithLogo()],
    ['the model list', 'llm', () => void fetchModelsForContext(ASSISTANT_CTX)],
  ])('%s is asked for again instead of joining the last session’s request', async (_name, fragment, call) => {
    call();
    await Promise.resolve();
    const before = callsTo(fragment);
    call();
    await Promise.resolve();
    expect(callsTo(fragment)).toBe(before);

    clearSessionCache();
    call();
    await Promise.resolve();

    expect(callsTo(fragment)).toBeGreaterThan(before);
  });

  it('the upload limit is asked for again and does not start from the last org’s value', async () => {
    http.client.get.mockResolvedValueOnce({ data: { maxFileSizeBytes: 5 * 1024 * 1024 } });
    const first = renderHook(() => useUploadLimits());
    await act(async () => {});
    expect(first.result.current.maxFileSizeBytes).toBe(5 * 1024 * 1024);
    first.unmount();
    const pendingBefore = callsTo('limits');

    // The next mount's request is still out when the session ends.
    renderHook(() => useUploadLimits()).unmount();
    clearSessionCache();

    const next = renderHook(() => useUploadLimits());
    expect(next.result.current.maxFileSizeBytes).toBe(DEFAULT_MAX_FILE_SIZE_BYTES);
    expect(callsTo('limits')).toBe(pendingBefore + 2);
  });
});
