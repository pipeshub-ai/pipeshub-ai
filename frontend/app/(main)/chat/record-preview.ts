import { KnowledgeBaseApi } from '@/knowledge-base/api';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import {
  acquireRecordForPreview,
  isDocxFile,
  isLegacyWordDocFile,
  isPresentationFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';
import { openRecordContent, PartyLeftError, SessionEndedError, type Pending, type Variant } from '@/lib/record-content';
import { useChatStore, type ChatPreviewFile } from './store';

/** A record to show in the house source preview: a cited document, a palette result, a job's file. */
export interface RecordPreviewTarget {
  recordId: string;
  recordName: string;
  /** What the caller already knows of it; the record's own details win. */
  mimeType?: string;
  extension?: string;
  initialPage?: number;
  highlightBox?: PreviewCitation['boundingBox'];
  citations?: PreviewCitation[];
  initialCitationId?: string;
}

interface PreviewSetters {
  setPreviewFile: (file: ChatPreviewFile | null) => void;
  setPreviewMode: (mode: 'sidebar' | 'fullscreen') => void;
}

/** Newest load of the chat preview. A load that is no longer current drops its answer. */
let loadToken = 0;
let activePending: Pending | null = null;

/** Which stream a load would ask for, from what the caller knows of the record; null when it cannot tell. */
export function variantOfTarget(target: Pick<RecordPreviewTarget, 'recordName' | 'mimeType' | 'extension'>): Variant | null {
  if (!target.mimeType && !target.extension) return null;
  const mime = target.mimeType || target.extension || '';
  return isPresentationFile(mime, target.recordName) || isLegacyWordDocFile(mime, target.recordName) ? 'pdf' : 'original';
}

function sameCitationSet(a: PreviewCitation[] | undefined, b: PreviewCitation[]): boolean {
  if (!a || a.length !== b.length) return false;
  return a.every((x, i) => x.id === b[i].id && x.content === b[i].content);
}

interface Anchor {
  initialPage?: number;
  highlightBox?: PreviewCitation['boundingBox'];
  initialCitationId?: string;
  citations: PreviewCitation[];
}

/** The anchor now shown for this record, or the one the load began with when another record is shown. */
function currentAnchor(recordId: string, fallback: Anchor): Anchor {
  const cur = useChatStore.getState().previewFile;
  if (!cur || cur.id !== recordId) return fallback;
  return {
    initialPage: cur.initialPage,
    highlightBox: cur.highlightBox,
    initialCitationId: cur.initialCitationId,
    citations: cur.citations ?? fallback.citations,
  };
}

/**
 * Opens a record in the file preview: shown loading at once, then the record's details (a record that cannot be
 * drawn skips the stream and offers its source), then the file from the record-content cache. The record already on
 * screen only moves to the new anchor. A failure is shown in the preview, never thrown. One path for every caller,
 * so the stream's options (PDF conversion and its fallback, DOCX blobs, cached leases) never drift between them.
 */
export async function loadRecordPreview(target: RecordPreviewTarget, store: PreviewSetters): Promise<void> {
  const { setPreviewFile, setPreviewMode } = store;
  const recordCitations = target.citations ?? [];
  const variant = variantOfTarget(target);
  const current = useChatStore.getState().previewFile;
  const anchor: Anchor = {
    initialPage: target.initialPage,
    highlightBox: target.highlightBox,
    initialCitationId: target.initialCitationId,
    citations: current && sameCitationSet(current.citations, recordCitations) ? current.citations : recordCitations,
  };
  if (current && current.id === target.recordId && variant && current.variant === variant && !current.error) {
    // The record is on screen, or its load is running: only the anchor moves. No request, no remount.
    setPreviewFile({
      ...current,
      ...anchor,
      jumpSeq: current.isLoading ? current.jumpSeq : (current.jumpSeq ?? 0) + 1,
    });
    return;
  }

  const token = ++loadToken;
  const isCurrent = () => token === loadToken;
  activePending?.leave();
  activePending = null;

  try {
    // Show loading state immediately (replacing a preview releases its lease).
    setPreviewFile({
      id: target.recordId,
      name: target.recordName,
      url: '',
      type: target.mimeType || target.extension || '',
      isLoading: true,
      variant: variant ?? undefined,
      jumpSeq: 0,
      ...anchor,
      citations: recordCitations,
    });
    setPreviewMode('sidebar');

    // Details first, so a record that cannot be drawn skips the stream. This call also mints the grant
    // that lets a connector record's retained copy be reused.
    const { details: recordDetails, grant } = await KnowledgeBaseApi.getRecordDetailsForOpen(target.recordId);
    if (!isCurrent()) return;
    const record = recordDetails?.record;
    if (!record) {
      throw new Error('Record details unavailable');
    }
    if (record.previewRenderable === false) {
      setPreviewFile({
        id: target.recordId,
        name: target.recordName,
        url: '',
        type: record.mimeType || target.mimeType || target.extension || '',
        size: record.sizeInBytes,
        isLoading: false,
        recordDetails,
        webUrl: record.webUrl ?? undefined,
        previewRenderable: false,
        variant: variant ?? undefined,
        jumpSeq: 0,
        ...currentAnchor(target.recordId, anchor),
      });
      return;
    }

    // Fresh record metadata decides the conversion; what the caller knew is the fallback.
    const previewMime = record.mimeType || target.mimeType || '';
    const previewName = record.recordName || target.recordName;
    const opened = await acquireRecordForPreview(
      async (want) => {
        const pending = openRecordContent(target.recordId, want, grant ?? undefined);
        activePending = pending;
        const lease = await pending.promise;
        if (!isCurrent()) {
          lease.release();
          return null;
        }
        activePending = null;
        return lease;
      },
      previewMime,
      previewName,
    );
    if (!opened) return;
    const { lease, asPdf } = opened;

    const resolvedType = resolvePreviewMimeAfterStream(
      record.mimeType || target.extension || '',
      previewName,
      lease.blob,
      asPdf,
    );
    // DOCX is handed to its renderer as the Blob itself: a blob URL fetched back renders blank.
    const isDocx = isDocxFile(record.mimeType, target.recordName, record.recordName, target.extension, record.fileRecord?.extension);

    // Land on the anchor the user chose last, not the one this load started with.
    const latest = useChatStore.getState().previewFile;
    setPreviewFile({
      id: target.recordId,
      name: target.recordName,
      url: isDocx ? '' : lease.url,
      blob: isDocx ? lease.blob : undefined,
      lease,
      variant: asPdf ? 'pdf' : 'original',
      type: resolvedType,
      size: record.sizeInBytes,
      isLoading: false,
      recordDetails,
      webUrl: record.webUrl ?? undefined,
      previewRenderable: record.previewRenderable,
      jumpSeq: (latest?.jumpSeq ?? 0) + 1,
      ...currentAnchor(target.recordId, anchor),
    });
  } catch (error) {
    if (!isCurrent()) return;
    if (error instanceof PartyLeftError || error instanceof SessionEndedError) return;
    console.error('Failed to load file preview:', error);
    setPreviewFile({
      id: target.recordId,
      name: target.recordName,
      url: '',
      type: target.mimeType || '',
      error: error instanceof Error ? error.message : 'Failed to load file',
      isLoading: false,
    });
  }
}

/** The same, on the chat store's preview. */
export function openRecordPreview(target: RecordPreviewTarget): Promise<void> {
  const { setPreviewFile, setPreviewMode } = useChatStore.getState();
  return loadRecordPreview(target, { setPreviewFile, setPreviewMode });
}
