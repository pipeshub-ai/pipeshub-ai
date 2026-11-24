/**
 * A large PDF on the record page is read by byte ranges: no whole-body request, the renderer gets the transport,
 * and anything but a partial answer opens the whole record through the real content cache.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
const small = { record: { ...big.record, sizeInBytes: 1024 * 1024 } };
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
const { getRecordContentCache } = await import('@/lib/record-content/cache');
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

describe('a large PDF on the record page', () => {
  it('is read by ranges: the renderer gets the transport and no whole-body request is made', async () => {
    mount();
    await waitFor(() => expect(rendered.some((r) => r.rangeTransport === transport)).toBe(true));
    expect(calls).toEqual([]);
    expect(rendered.at(-1)?.fileUrl).toBe('');
  });

  it('destroys the transport when the page goes away', async () => {
    const view = mount();
    await waitFor(() => expect(rendered.some((r) => r.rangeTransport === transport)).toBe(true));
    view.unmount();
    expect(rangeOpen.destroy).toHaveBeenCalled();
  });

  it('opens the whole record through the cache when the server does not answer with a partial response', async () => {
    rangeOpen.result = { mode: 'whole' };
    mount();
    await waitFor(() => expect(rendered.some((r) => r.fileUrl === 'blob:r/1')).toBe(true));
    expect(calls).toEqual([{ variant: 'original' }]);
    expect(rendered.every((r) => r.rangeTransport === undefined)).toBe(true);
  });

  it('opens the whole record once range mode ends (a changed document)', async () => {
    mount();
    await waitFor(() => expect(rangeOpen.onFallback).not.toBeNull());
    await waitFor(() => expect(rendered.some((r) => r.rangeTransport === transport)).toBe(true));
    rangeOpen.onFallback?.('changed');
    await waitFor(() => expect(rendered.some((r) => r.fileUrl === 'blob:r/1')).toBe(true));
    expect(calls).toEqual([{ variant: 'original' }]);
    expect(rangeOpen.calls).toBe(1); // not tried again for this record
    expect(rangeOpen.destroy).toHaveBeenCalled();
  });

  it('a PDF under the threshold never tries ranges', async () => {
    details.current = small;
    mount();
    await waitFor(() => expect(calls).toEqual([{ variant: 'original' }]));
    expect(rangeOpen.calls).toBe(0);
  });

  it('with the cache switched off, never tries ranges', async () => {
    const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
    useFeatureFlagsStore.setState({ flags: { ENABLE_RECORD_PREVIEW_CACHE: false } });
    mount();
    await waitFor(() => expect(calls).toEqual([{ variant: 'original' }]));
    expect(rangeOpen.calls).toBe(0);
    useFeatureFlagsStore.setState({ flags: null });
  });

  it('downloads a range-mode file from its own object URL and holds no cache entry while the browser reads it', async () => {
    let n = 0;
    URL.createObjectURL = vi.fn(() => `blob:r/${(n += 1)}`);
    const hrefs: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      hrefs.push(this.href);
    });
    mount();
    await waitFor(() => expect(rendered.some((r) => r.rangeTransport === transport)).toBe(true));
    fireEvent.click(screen.getAllByRole('button', { name: /download/i })[0]);
    await waitFor(() => expect(hrefs).toHaveLength(1));
    click.mockRestore();
    expect(calls).toEqual([{ variant: 'original' }]);
    // The page's hold on the entry is gone as soon as the bytes are taken, and the URL the download reads is
    // never revoked by that release.
    expect(getRecordContentCache().snapshot().entries.filter((e) => e.pins > 0)).toEqual([]);
    expect(vi.mocked(URL.revokeObjectURL).mock.calls.map((c) => c[0])).not.toContain(hrefs[0]);
  });
});
