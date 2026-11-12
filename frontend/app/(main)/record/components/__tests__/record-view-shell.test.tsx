import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

const env = vi.hoisted(() => ({ mobile: true, inAppBack: false, back: vi.fn(), push: vi.fn() }));
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
vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: {
    getRecordDetails: vi.fn(async () => ({
      record: { id: 'r1', recordName: 'Plan.pdf', recordType: 'FILE', mimeType: 'application/pdf', origin: 'UPLOAD' },
    })),
    streamRecord: vi.fn(async () => new Blob(['x'], { type: 'application/pdf' })),
    deleteRecord: vi.fn(async () => undefined),
  },
}));

import { RecordViewShell } from '../record-view-shell';

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

describe('RecordViewShell on phones', () => {
  it('hides the zoom bar and keeps metadata behind a collapsible section', async () => {
    env.mobile = true;
    renderShell();
    await waitFor(() => expect(screen.getByTestId('preview')).toBeTruthy());
    expect(screen.queryByTestId('zoom-bar')).toBeNull();
    expect(screen.queryByTestId('metadata')).toBeNull();
    screen.getByRole('button', { name: /Record information/ }).click();
    await waitFor(() => expect(screen.getByTestId('metadata')).toBeTruthy());
  });

  it('shows the zoom bar and metadata side by side on desktop', async () => {
    env.mobile = false;
    renderShell();
    await waitFor(() => expect(screen.getByTestId('metadata')).toBeTruthy());
    expect(screen.getByTestId('zoom-bar')).toBeTruthy();
  });

  it('Back goes to the knowledge base on the web, even with history from another site', async () => {
    env.mobile = true;
    env.inAppBack = false;
    env.back.mockClear();
    env.push.mockClear();
    const spy = vi.spyOn(window.history, 'length', 'get').mockReturnValue(3);
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }));
    expect(env.push).toHaveBeenCalledWith('/knowledge-base/');
    expect(env.back).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it('Back returns to the previous screen in the desktop app', async () => {
    env.mobile = true;
    env.inAppBack = true;
    env.back.mockClear();
    env.push.mockClear();
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }));
    expect(env.back).toHaveBeenCalledTimes(1);
    expect(env.push).not.toHaveBeenCalled();
    env.inAppBack = false;
  });

  it('offers Back on web tablets and desktops too', async () => {
    env.mobile = false;
    env.inAppBack = false;
    env.push.mockClear();
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: 'Back' }));
    expect(env.push).toHaveBeenCalledWith('/knowledge-base/');
  });

  it('deleting the record returns to its collection, not to chat', async () => {
    env.mobile = false;
    env.inAppBack = false;
    env.push.mockClear();
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockResolvedValueOnce({
      record: { id: 'r1', recordName: 'Plan.pdf', recordType: 'FILE', mimeType: 'application/pdf', origin: 'UPLOAD' },
      knowledgeBase: { id: 'kb-1', name: 'Docs', orgId: 'o' },
    } as never);
    renderShell();
    fireEvent.click(await screen.findByRole('button', { name: /Delete/ }));
    fireEvent.change(await screen.findByPlaceholderText('DELETE'), { target: { value: 'DELETE' } });
    const buttons = await screen.findAllByRole('button', { name: /Delete/ });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(env.push).toHaveBeenCalledWith('/knowledge-base/?nodeType=app&nodeId=kb-1'));
  });
});

describe('RecordViewShell when the record cannot be read', () => {
  it.each([403, 404])('shows the one no-access page for a %i, naming nothing', async (status) => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockRejectedValueOnce({ type: 'NOT_FOUND', message: 'Record Plan.pdf was deleted', statusCode: status });
    renderShell();

    expect(await screen.findByTestId('no-access-page')).toBeTruthy();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(document.body.textContent).not.toContain('Plan.pdf');
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('keeps the ordinary error for a failure that says nothing about access', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockRejectedValueOnce({ type: 'SERVER_ERROR', message: 'boom', statusCode: 500 });
    renderShell();

    await waitFor(() => expect(screen.queryByText('loading')).toBeNull());
    expect(screen.queryByTestId('no-access-page')).toBeNull();
  });

  it('keeps the preview handling when only the stream says 404: the record opens, it is not a no-access page', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    vi.mocked(KnowledgeBaseApi.streamRecord).mockRejectedValue({ type: 'NOT_FOUND', message: 'file gone', statusCode: 404 });
    renderShell();

    await waitFor(() => expect(screen.queryByText('loading')).toBeNull());
    expect(screen.queryByTestId('no-access-page')).toBeNull();
    expect(await screen.findByTestId('metadata')).toBeTruthy();
    vi.mocked(KnowledgeBaseApi.streamRecord).mockImplementation(async () => new Blob(['x'], { type: 'application/pdf' }));
  });

  it('asks for no toast on a refusal, so the server\'s words cannot confirm the record exists', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    const { useToastStore } = await import('@/lib/store/toast-store');
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockClear();
    renderShell();
    await screen.findByTestId('metadata');

    const options = vi.mocked(KnowledgeBaseApi.getRecordDetails).mock.calls[0][1] as { suppressErrorToast: (e: { statusCode?: number }) => boolean };
    expect([403, 404, 500, undefined].map((statusCode) => options.suppressErrorToast({ statusCode }))).toEqual([true, true, false, false]);
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('looks the same for a 403 and a 404', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    const seen: string[] = [];
    for (const statusCode of [403, 404]) {
      vi.mocked(KnowledgeBaseApi.getRecordDetails).mockRejectedValueOnce({ type: 'NOT_FOUND', message: `status ${statusCode} words`, statusCode });
      const { unmount } = renderShell();
      seen.push((await screen.findByTestId('no-access-page')).outerHTML);
      unmount();
    }
    expect(seen[0]).toBe(seen[1]);
  });

  it('gives the no-access page a Back that leaves it: the collections, when the record is unknown', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    env.mobile = false;
    env.inAppBack = false;
    env.push.mockClear();
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockRejectedValueOnce({ type: 'NOT_FOUND', message: 'gone', statusCode: 404 });
    renderShell();

    fireEvent.click(await screen.findByRole('button', { name: 'Back' }));

    expect(env.push).toHaveBeenCalledWith('/knowledge-base/');
  });

  it('goes back in the app, where the app has a history, from the no-access page', async () => {
    const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
    env.inAppBack = true;
    env.back.mockClear();
    vi.mocked(KnowledgeBaseApi.getRecordDetails).mockRejectedValueOnce({ type: 'NOT_FOUND', message: 'gone', statusCode: 403 });
    renderShell();

    fireEvent.click(await screen.findByRole('button', { name: 'Back' }));

    expect(env.back).toHaveBeenCalledOnce();
    env.inAppBack = false;
  });
});
