/**
 * The first range request of a large PDF fails (a 5xx, the network, a timeout): the page opens the whole record
 * through the content cache, and shows an error only when that load fails too.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});
vi.mock('@/lib/electron/in-app-back', () => ({ canGoBackInApp: () => false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false, useIsBelowDesktop: () => false }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ back: vi.fn(), push: vi.fn() }) }));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));
const rendered = vi.hoisted(() => [] as Array<{ rangeTransport?: unknown; fileUrl: string }>);
vi.mock('@/app/components/file-preview/renderers/file-preview-renderer', () => ({
  FilePreviewRenderer: (props: { rangeTransport?: unknown; fileUrl: string }) => {
    rendered.push({ rangeTransport: props.rangeTransport, fileUrl: props.fileUrl });
    return <div data-testid="preview" />;
  },
}));
vi.mock('../record-metadata-panel', () => ({ RecordMetadataPanel: () => <div data-testid="metadata" /> }));
vi.mock('../zoom-action-bar', () => ({ ZoomActionBar: () => <div data-testid="zoom-bar" /> }));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));

const big = {
  record: {
    id: 'r1',
    recordName: 'Atlas.pdf',
    recordType: 'FILE',
    mimeType: 'application/pdf',
    origin: 'UPLOAD',
    sizeInBytes: 200 * 1024 * 1024,
  },
};
const details = vi.hoisted(() => ({ current: null as unknown }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    getRecordDetails: vi.fn(async () => details.current),
    getRecordDetailsForOpen: vi.fn(async () => ({ details: details.current, grant: null })),
    deleteRecord: vi.fn(async () => undefined),
  },
}));

type Call = { variant: string };
const calls: Call[] = [];
vi.mock('@/lib/record-content/fetch', () => ({
  fetchRecordContent: vi.fn(async (args: { variant: string }) => {
    calls.push({ variant: args.variant });
    return { status: 200, blob: new Blob(['%PDF'], { type: 'application/pdf' }), etag: '"o"', mime: 'application/pdf' };
  }),
}));

const rangeOpen = vi.hoisted(() => ({
  result: null as unknown,
  onFallback: null as null | ((reason: string) => void),
  destroy: vi.fn(),
  calls: 0,
}));
vi.mock('@/lib/record-content/pdf-range-transport', async () => {
  const actual = await vi.importActual<typeof import('@/lib/record-content/pdf-range-transport')>('@/lib/record-content/pdf-range-transport');
  return {
    ...actual,
    openRecordRange: vi.fn(async (o: { onFallback: (r: string) => void }) => {
      rangeOpen.calls += 1;
      rangeOpen.onFallback = o.onFallback;
      return rangeOpen.result;
    }),
  };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { clearRecordContent } = await import('@/lib/record-content');
const { RecordViewShell } = await import('../record-view-shell');

const transport = { length: 200 * 1024 * 1024, destroy: rangeOpen.destroy };

beforeEach(() => {
  calls.length = 0;
  rendered.length = 0;
  rangeOpen.calls = 0;
  rangeOpen.destroy.mockClear();
  rangeOpen.result = { mode: 'range', transport, etag: '"v"', length: transport.length };
  details.current = big;
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
  URL.createObjectURL = vi.fn(() => 'blob:r/1');
  URL.revokeObjectURL = vi.fn();
  clearRecordContent();
});
afterEach(cleanup);

const mount = () =>
  render(
    <Theme>
      <RecordViewShell recordId="r1" />
    </Theme>,
  );

describe('the first range request fails', () => {
  it.each(['Request failed with status code 500', 'Network Error', 'timeout of 30000ms exceeded'])(
    '%s: the page opens the whole record through the cache instead of failing',
    async (message) => {
      const { openRecordRange } = await import('@/lib/record-content/pdf-range-transport');
      vi.mocked(openRecordRange).mockImplementationOnce(async () => {
        rangeOpen.calls += 1;
        throw Object.assign(new Error(message), { statusCode: message.includes('500') ? 500 : undefined });
      });
      const view = mount();
      await waitFor(() => expect(rangeOpen.calls).toBe(1));
      await waitFor(() => expect(rendered.some((r) => r.fileUrl === 'blob:r/1')).toBe(true));
      expect(calls).toEqual([{ variant: 'original' }]);
      expect(view.container.textContent ?? '').not.toContain(message);
    },
  );

  it('shows the error when the whole-file load fails too', async () => {
    const { openRecordRange } = await import('@/lib/record-content/pdf-range-transport');
    const { fetchRecordContent } = await import('@/lib/record-content/fetch');
    vi.mocked(openRecordRange).mockImplementationOnce(async () => {
      rangeOpen.calls += 1;
      throw new Error('Network Error');
    });
    vi.mocked(fetchRecordContent).mockRejectedValueOnce(new Error('whole file failed'));
    const view = mount();
    await waitFor(() => expect(view.container.textContent ?? '').toContain('whole file failed'));
    expect(rendered.some((r) => r.fileUrl === 'blob:r/1')).toBe(false);
  });
});
