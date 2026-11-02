import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import '@/lib/__tests__/test-i18n';
import { ErrorType } from '@/lib/api';
import { installBrowserShims, renderInTheme } from '@/app/(main)/agents/agent-builder/__tests__/agent-builder-harness';

const kb = vi.hoisted(() => ({ streamRecord: vi.fn(), streamDownloadRecord: vi.fn(), getRecordDetails: vi.fn() }));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeBaseApi: kb }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/app/(main)/workspace/mcp-servers/api', () => ({ McpServersApi: {} }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: vi.fn() }));

import { ChatResponse } from '../chat-response';
import { useChatStore } from '../../../store';

const EVIL = 'https://evil.example/steal.csv';
const SIGNED = 'https://bucket.s3.amazonaws.com/data.csv?X-Amz-Signature=abc';

function answerWith(url: string, recordId = 'rec-1') {
  return `Done. ::artifact[data.csv](${url}){text/csv|doc-1|${recordId}|DATA_FILE|1}`;
}

function failure(statusCode: number) {
  return { type: ErrorType.SERVER_ERROR, message: 'boom', statusCode };
}

beforeEach(() => {
  installBrowserShims();
  useChatStore.getState().setPreviewFile(null);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function clickPreview(answer: string) {
  renderInTheme(<ChatResponse question="Q" answer={answer} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Preview artifact' }));
}

describe('ChatResponse artifact preview', () => {
  it.each([
    ['a plain URL', EVIL],
    ['a URL that only looks signed', 'https://evil.example/steal.csv?X-Amz-Signature=x'],
  ])('never previews an untrusted marker URL when the stream fails: %s', async (_label, url) => {
    kb.streamRecord.mockRejectedValue(failure(500));

    await clickPreview(answerWith(url));

    await waitFor(() => expect(kb.streamRecord).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview artifact' })).toBeTruthy());
    expect(useChatStore.getState().previewFile?.url).not.toBe(url);
  });

  it('never previews an untrusted marker URL when there is no record', async () => {
    await clickPreview(answerWith(EVIL, ''));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview artifact' })).toBeTruthy());
    expect(useChatStore.getState().previewFile?.url).not.toBe(EVIL);
  });

  it('still falls back to a signed URL when the stream fails', async () => {
    kb.streamRecord.mockRejectedValue(failure(500));

    await clickPreview(answerWith(SIGNED));

    await waitFor(() => expect(useChatStore.getState().previewFile?.url).toBe(SIGNED));
  });

  it('marks the card unavailable when the artifact was deleted', async () => {
    kb.streamRecord.mockRejectedValue(failure(404));

    await clickPreview(answerWith(SIGNED));

    expect(await screen.findByText('No longer available')).toBeTruthy();
    expect(useChatStore.getState().previewFile?.url).not.toBe(SIGNED);
    expect(kb.streamRecord).toHaveBeenCalledWith('rec-1', expect.objectContaining({ suppressErrorToast: expect.any(Function) }));
  });
});
