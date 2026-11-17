/**
 * the record page's hold on the cached content, through the REAL record-content cache.
 * Only the cache's transport (fetch.ts) and the details call are faked.
 *
 * Attack: unmount (or switch record) while a PowerPoint's PDF conversion load is pending. The page's
 * `revokeBlobUrl` leaves the pending open, which rejects with PartyLeftError; the conversion
 * fallback must not then open the original and keep a lease nobody releases.
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
vi.mock('@/app/components/file-preview/renderers/file-preview-renderer', () => ({
  FilePreviewRenderer: () => <div data-testid="preview" />,
}));
vi.mock('../record-metadata-panel', () => ({ RecordMetadataPanel: () => <div data-testid="metadata" /> }));
vi.mock('../zoom-action-bar', () => ({ ZoomActionBar: () => <div data-testid="zoom-bar" /> }));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));

const pptx = {
  record: {
    id: 'r1',
    recordName: 'Deck.pptx',
    recordType: 'FILE',
    mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    origin: 'UPLOAD',
  },
};
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    getRecordDetails: vi.fn(async () => pptx),
    getRecordDetailsForOpen: vi.fn(async () => ({ details: pptx, grant: null })),
    deleteRecord: vi.fn(async () => undefined),
  },
}));

type Call = { variant: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
const calls: Call[] = [];
vi.mock('@/lib/record-content/fetch', () => ({
  fetchRecordContent: vi.fn(
    (args: { variant: string }) =>
      new Promise((resolve, reject) => {
        calls.push({ variant: args.variant, resolve, reject });
      }),
  ),
}));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { clearRecordContent } = await import('@/lib/record-content');
const { getRecordContentCache } = await import('@/lib/record-content/cache');
const { RecordViewShell } = await import('../record-view-shell');

let live = 0;
beforeEach(() => {
  calls.length = 0;
  live = 0;
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
  URL.createObjectURL = vi.fn(() => {
    live += 1;
    return `blob:qa/${live}`;
  });
  URL.revokeObjectURL = vi.fn(() => {
    live -= 1;
  });
  clearRecordContent();
});
afterEach(cleanup);

const ok = (tag: string) => ({ status: 200, blob: new Blob(['%PDF'], { type: 'application/pdf' }), etag: tag, mime: 'application/pdf' });

describe('record page lease lifecycle (real cache)', () => {
  it('unmount during a conversion load opens nothing more and leaves no lease held', async () => {
    const view = render(
      <Theme>
        <RecordViewShell recordId="r1" />
      </Theme>,
    );
    await waitFor(() => expect(calls.map((c) => c.variant)).toEqual(['pdf']));

    view.unmount();
    // Let the PartyLeftError settle through the page's catch blocks.
    await new Promise((r) => setTimeout(r, 0));

    // The conversion lands later (its job ran on, as designed) and is retained unpinned.
    calls[0].resolve(ok('W/"p"'));
    await new Promise((r) => setTimeout(r, 0));
    for (const c of calls.slice(1)) c.resolve(ok('"o"'));
    await new Promise((r) => setTimeout(r, 0));

    const snap = getRecordContentCache().snapshot();
    // Expected: the page asked for nothing after it was gone, and holds no pin.
    expect.soft(calls.map((c) => c.variant), 'stream requests after unmount').toEqual(['pdf']);
    expect.soft(snap.entries.filter((e) => e.pins > 0).map((e) => e.variant), 'entries still pinned after unmount').toEqual([]);
  });

  // The open can settle before the page lets go and still be un-awaited by the page: the lease it holds must be
  // released by the page's own epoch check, since `leave()` on a settled open does nothing. The microtask at which the
  // page's continuation runs is not fixed, so leave at each of the first ticks after the content lands.
  it.each(Array.from({ length: 16 }, (_, ticks) => ticks))(
    'leaving %i microtasks after the content landed releases the lease',
    async (ticks) => {
      const view = render(
        <Theme>
          <RecordViewShell recordId="r1" />
        </Theme>,
      );
      await waitFor(() => expect(calls.map((c) => c.variant)).toEqual(['pdf']));
      calls[0].resolve(ok('"p"'));
      for (let i = 0; i < ticks; i += 1) await Promise.resolve();
      view.unmount();
      await new Promise((r) => setTimeout(r, 0));
      for (const c of calls.slice(1)) c.resolve(ok('"o"'));
      await new Promise((r) => setTimeout(r, 0));
      expect(getRecordContentCache().snapshot().entries.filter((e) => e.pins > 0)).toEqual([]);
    },
  );

  // A conversion that fails (not a leave) falls back to the original file. If the page is gone by the time the
  // failure reaches its catch, the fallback must not start: it would take a lease nobody releases.
  it.each(Array.from({ length: 16 }, (_, ticks) => ticks))(
    'leaving %i microtasks after the conversion failed leaves no lease on the fallback',
    async (ticks) => {
      const view = render(
        <Theme>
          <RecordViewShell recordId="r1" />
        </Theme>,
      );
      await waitFor(() => expect(calls.map((c) => c.variant)).toEqual(['pdf']));
      calls[0].reject(new Error('conversion failed'));
      for (let i = 0; i < ticks; i += 1) await Promise.resolve();
      view.unmount();
      await new Promise((r) => setTimeout(r, 0));
      for (const c of calls.slice(1)) c.resolve(ok('"o"'));
      await new Promise((r) => setTimeout(r, 0));
      expect(getRecordContentCache().snapshot().entries.filter((e) => e.pins > 0)).toEqual([]);
    },
  );
});
