/**
 * The upload tracker's rows and the summary toast for rejected files: a row
 * only shows success when the server confirmed it, and a failed batch says
 * why and what to do.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

const { useUploadStore } = await import('../upload-store');
const { useToastStore } = await import('../toast-store');
const { useUserStore } = await import('../user-store');
const { notifyUploadFailures } = await import('@/lib/utils/upload-failure-feedback');
const { FileRejectionReason } = await import('@/lib/constants/file-rejection-reason');

const upload = () => useUploadStore.getState();
const item = (id: string) => upload().items.find((i) => i.id === id);

function addRows(uploadId: string, ...ids: string[]) {
  upload().upsertSession({ uploadId, kbId: 'kb-1', folderId: null, label: `${ids.length} files` });
  upload().addItems(
    ids.map((id) => ({ id, name: `${id}.pdf`, type: 'file' as const, size: 100, knowledgeBaseId: 'kb-1', parentId: null, uploadId })),
  );
}

beforeEach(() => {
  upload().clearAll();
  useToastStore.setState({ toasts: [] });
  useUserStore.setState({ profile: null });
});

describe('upload tracker rows', () => {
  it('shows the tray with totals as soon as files are added', () => {
    addRows('u1', 'a', 'b');
    expect(upload()).toMatchObject({ isVisible: true, totalCount: 2, totalSize: 200, completedCount: 0 });
    expect(item('a')).toMatchObject({ status: 'pending', progress: 0 });
  });

  it('never turns a failed row green when a late success arrives', () => {
    addRows('u1', 'a');
    upload().startUpload('a');
    upload().failUpload('a', ['  ', 'Unsupported file type'], [FileRejectionReason.UNSUPPORTED_TYPE]);
    upload().completeUpload('a');
    expect(item('a')).toMatchObject({ status: 'failed', errors: ['Unsupported file type'], rejectionReasons: ['UNSUPPORTED_TYPE'] });
    expect(upload().completedCount).toBe(0);
  });

  it('fails rows the server never confirmed when the batch ends, and leaves settled ones alone', () => {
    addRows('u1', 'a', 'b', 'c');
    addRows('u2', 'other');
    upload().startUpload('a');
    upload().startUpload('b');
    upload().completeUpload('b');

    upload().finalizeSession('u1', 'The connection dropped before this file finished. Upload it again.');

    expect(item('a')).toMatchObject({ status: 'failed', errors: ['The connection dropped before this file finished. Upload it again.'] });
    expect(item('b')?.status).toBe('completed');
    expect(item('c')?.status).toBe('failed');
    expect(item('other')?.status).toBe('pending');
    expect(upload().sessions.u1.status).toBe('done');
  });

  it('clearing completed rows keeps failures and forgets sessions with nothing left', () => {
    addRows('u1', 'a');
    addRows('u2', 'b');
    upload().completeUpload('a');
    upload().failUpload('b', 'Too large');
    upload().clearCompleted();
    expect(upload().items.map((i) => i.id)).toEqual(['b']);
    expect(Object.keys(upload().sessions)).toEqual(['u2']);
    expect(upload().clearedCompletedCount).toBe(1);
    upload().removeItem('b');
    expect(upload().isVisible).toBe(false);
  });

  it('keeps a session start time when it is updated', () => {
    upload().upsertSession({ uploadId: 'u1', kbId: 'kb', folderId: null, label: 'x', createdAt: 5 });
    upload().upsertSession({ uploadId: 'u1', kbId: 'kb', folderId: null, label: 'y', status: 'done' });
    expect(upload().sessions.u1).toMatchObject({ createdAt: 5, label: 'y', status: 'done' });
  });

  it('updates several rows at once', () => {
    addRows('u1', 'a', 'b');
    upload().bulkUpdateItemStatus(['a', 'b'], 'completed', 100);
    upload().updateItemStatus('b', 'failed', 0, 'Server error');
    expect(upload().completedCount).toBe(1);
    expect(item('b')?.errors).toEqual(['Server error']);
  });
});

describe('the rejected-files toast', () => {
  const failed = (...reasons: Array<string | undefined>) =>
    reasons.map((reason, i) => ({
      id: `f${i}`,
      name: `f${i}`,
      type: 'file' as const,
      size: 1,
      status: 'failed' as const,
      progress: 0,
      knowledgeBaseId: 'kb',
      parentId: null,
      rejectionReasons: reason ? [reason as never] : undefined,
    }));
  const toasts = () => useToastStore.getState().toasts;

  it('says nothing when nothing failed', () => {
    notifyUploadFailures([], 30);
    expect(toasts()).toHaveLength(0);
  });

  it('names the size limit and tells a member who to ask', () => {
    notifyUploadFailures(failed('EXCEEDS_SIZE_LIMIT', 'EXCEEDS_SIZE_LIMIT'), 30);
    const [t] = toasts();
    expect(t).toMatchObject({ variant: 'warning', title: '2 files exceed the size limit', placement: 'top', duration: null });
    expect(t.description).toContain('30 MB');
    expect(t.description).toMatch(/administrator/);
    expect(t.action).toBeUndefined();
  });

  it('gives an admin a link to raise the limit', () => {
    useUserStore.setState({ profile: { isAdmin: true } as never });
    notifyUploadFailures(failed('EXCEEDS_SIZE_LIMIT'), 30);
    expect(toasts()[0].description).toMatch(/Workspace Labs/);
    expect(toasts()[0].action).toMatchObject({ href: expect.stringContaining('/workspace/labs/'), openInNewTab: true });
  });

  it('summarises a mix of reasons, leading with the most specific', () => {
    notifyUploadFailures(failed('UNSUPPORTED_TYPE', 'DUPLICATE_NAME', 'DUPLICATE_NAME', undefined), 30);
    const [t] = toasts();
    expect(t).toMatchObject({ variant: 'warning', title: "4 files can't be uploaded" });
    expect(t.description).toContain('1 unsupported type · 2 duplicate names · 1 other error');
  });

  it('treats only informational rejections as info', () => {
    notifyUploadFailures(failed('DUPLICATE_NAME'), 30);
    expect(toasts()[0]).toMatchObject({ variant: 'info', title: "1 file can't be uploaded" });
  });
});
