import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const env = vi.hoisted(() => ({ mobile: false, inAppBack: false, status: 'COMPLETED', back: vi.fn(), push: vi.fn() }));
vi.mock('@/lib/electron/in-app-back', () => ({ canGoBackInApp: () => env.inAppBack }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({
  useIsMobile: () => env.mobile,
  useIsBelowDesktop: () => env.mobile,
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ back: env.back, push: env.push }) }));
vi.mock('@/app/components/theme-provider', () => ({ useThemeAppearance: () => ({ appearance: 'light' }) }));
vi.mock('@/app/components/file-preview/renderers/file-preview-renderer', () => ({
  FilePreviewRenderer: () => <div data-testid="preview" />,
}));
vi.mock('../record-metadata-panel', () => ({ RecordMetadataPanel: () => <div data-testid="metadata" /> }));
vi.mock('../zoom-action-bar', () => ({ ZoomActionBar: () => <div data-testid="zoom-bar" /> }));
vi.mock('@/app/components/ui/auth-guard', () => ({ LoadingScreen: () => <div>loading</div> }));
vi.mock('@/app/(main)/knowledge-base/api', () => {
  const getRecordDetails = vi.fn(async (..._args: unknown[]) => ({
    record: { id: 'r1', recordName: 'Plan.pdf', recordType: 'FILE', mimeType: 'application/pdf', origin: 'UPLOAD', indexingStatus: env.status },
  }));
  return {
    KnowledgeBaseApi: {
      getRecordDetails,
      getRecordDetailsForOpen: async (...args: unknown[]) => ({ details: await getRecordDetails(...args), grant: null }),
      streamRecord: vi.fn(async () => new Blob(['x'], { type: 'application/pdf' })),
      deleteRecord: vi.fn(async () => undefined),
      reindexItem: vi.fn(async (..._args: unknown[]) => ({})),
    },
  };
});
// Content opens go through the record-content cache; its transport is the faked `streamRecord`.
vi.mock('@/lib/record-content', async () => {
  const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
  const { PartyLeftError, SessionEndedError } = await vi.importActual<typeof import('@/lib/record-content')>('@/lib/record-content');
  return {
    PartyLeftError,
    SessionEndedError,
    openRecordContent: (id: string, variant: string) => ({
      promise: KnowledgeBaseApi.streamRecord(id, variant === 'pdf' ? { convertTo: 'application/pdf' } : undefined).then(
        (blob: Blob) => ({ url: 'blob:x', blob, mime: blob.type, etag: null, release: () => {} }),
      ),
      leave: () => {},
    }),
  };
});

import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { RecordViewShell } from '../record-view-shell';

const reindexItem = KnowledgeBaseApi.reindexItem as unknown as ReturnType<typeof vi.fn>;

beforeAll(() => {
  URL.createObjectURL = () => 'blob:x';
  URL.revokeObjectURL = () => {};
});
afterEach(cleanup);

const renderShell = () =>
  render(
    <Theme>
      <RecordViewShell recordId="r1" />
    </Theme>,
  );


describe('RecordViewShell reindex', () => {
  it('a confirmed Force reindex of an indexed file is sent as force, whatever the status reads at confirm time', async () => {
    env.status = 'COMPLETED';
    reindexItem.mockClear();
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: 'Force reindex' }));
    const confirm = await screen.findByRole('button', { name: /Confirm/ });
    env.status = 'FAILED';                                     // the record reloads under the open dialog
    fireEvent.click(confirm);
    await waitFor(() => expect(reindexItem).toHaveBeenCalledTimes(1));
    expect(reindexItem).toHaveBeenCalledWith('r1', 0, undefined, true);
  });

  it('retrying a failed file is a plain reindex, not a forced one', async () => {
    env.status = 'FAILED';
    reindexItem.mockClear();
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry indexing' }));
    await waitFor(() => expect(reindexItem).toHaveBeenCalledTimes(1));
    expect(reindexItem).toHaveBeenCalledWith('r1');
  });
});
