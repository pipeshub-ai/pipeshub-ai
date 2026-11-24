/**
 * The chat preview holds a lease on the cached record content. Replacing or closing the preview
 * must release it: before, a new preview left the previous object URL alive for good.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { useChatStore } = await import('@/chat/store');
const { openRecordContent, clearRecordContent } = await import('@/lib/record-content');

const file = (id: string, lease?: { release(): void }) => ({ id, name: id, url: '', type: 'application/pdf', lease });

describe('chat preview lease', () => {
  beforeEach(() => {
    useChatStore.setState({ previewFile: null, previewMode: 'sidebar' });
  });

  it('releases the lease when the preview is replaced, closed or reset', () => {
    const a = { release: vi.fn() };
    const b = { release: vi.fn() };
    const { setPreviewFile, clearPreview, reset } = useChatStore.getState();

    setPreviewFile(file('a', a));
    // An in-place update that keeps the same lease holds on to it.
    setPreviewFile({ ...file('a', a), initialPage: 3 });
    expect(a.release).not.toHaveBeenCalled();

    setPreviewFile(file('b', b));
    expect(a.release).toHaveBeenCalledTimes(1);

    clearPreview();
    expect(b.release).toHaveBeenCalledTimes(1);

    const c = { release: vi.fn() };
    setPreviewFile(file('c', c));
    reset();
    expect(c.release).toHaveBeenCalledTimes(1);
  });

  it('keeps the number of live object URLs flat over ten previews', async () => {
    let n = 0;
    const live = new Set<string>();
    URL.createObjectURL = vi.fn(() => {
      const u = `blob:l/${++n}`;
      live.add(u);
      return u;
    });
    URL.revokeObjectURL = vi.fn((u: string) => void live.delete(u));
    useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
    clearRecordContent();
    const routes: Record<string, { status: number; data: Blob }> = {};
    for (let i = 0; i < 10; i++) {
      routes[`GET /api/v1/knowledgeBase/stream/record/r${i}`] = { status: 200, data: new Blob(['x'], { type: 'application/pdf' }) };
    }
    fakeApi(routes);

    for (let i = 0; i < 10; i++) {
      const lease = await openRecordContent(`r${i}`, 'original').promise;
      void lease.url;
      useChatStore.getState().setPreviewFile(file(`r${i}`, lease));
      expect(live.size).toBe(1);
    }
    useChatStore.getState().clearPreview();
    expect(live.size).toBe(0);
  });
});
