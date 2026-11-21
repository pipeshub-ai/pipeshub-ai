import { KnowledgeBaseApi } from '@/knowledge-base/api';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import {
  isDocxFile,
  resolvePreviewMimeAfterStream,
  streamRecordForPreview,
} from '@/app/components/file-preview/utils';
import { useChatStore, type ChatPreviewFile } from './store';

/** A record to show in the house source preview: a cited document, a palette result, a job's file. */
export interface RecordPreviewTarget {
  recordId: string;
  recordName: string;
  /** What the caller already knows of it; the record's own details win. */
  mimeType?: string;
  extension?: string;
  initialPage?: number;
  highlightBox?: Array<{ x: number; y: number }>;
  citations?: PreviewCitation[];
  initialCitationId?: string;
}

interface PreviewSetters {
  setPreviewFile: (file: ChatPreviewFile | null) => void;
  setPreviewMode: (mode: 'sidebar' | 'fullscreen') => void;
}

/**
 * Opens a record in the file preview: shown loading at once, then the record's details (a record that cannot be
 * drawn skips the stream and offers its source), then the streamed file. A failure is shown in the preview, never
 * thrown. One path for every caller, so the stream's options (PDF conversion, DOCX blobs) never drift between them.
 */
export async function loadRecordPreview(target: RecordPreviewTarget, store: PreviewSetters): Promise<void> {
  const { setPreviewFile, setPreviewMode } = store;
  const anchor = {
    ...(target.initialPage !== undefined ? { initialPage: target.initialPage } : {}),
    ...(target.highlightBox !== undefined ? { highlightBox: target.highlightBox } : {}),
    ...(target.citations !== undefined ? { citations: target.citations } : {}),
    ...(target.initialCitationId !== undefined ? { initialCitationId: target.initialCitationId } : {}),
  };
  try {
    setPreviewFile({
      id: target.recordId,
      name: target.recordName,
      url: '',
      type: target.mimeType || target.extension || '',
      isLoading: true,
      ...anchor,
    });
    setPreviewMode('sidebar');

    const recordDetails = await KnowledgeBaseApi.getRecordDetails(target.recordId);
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
        ...anchor,
      });
      return;
    }

    const previewMime = record.mimeType || target.mimeType || '';
    const previewName = record.recordName || target.recordName;
    const { blob, asPdf } = await streamRecordForPreview(
      (options) => KnowledgeBaseApi.streamRecord(target.recordId, options),
      previewMime,
      previewName,
    );
    const resolvedType = resolvePreviewMimeAfterStream(record.mimeType || target.extension || '', previewName, blob, asPdf);
    // DOCX is handed to its renderer as the Blob itself: a blob URL fetched back renders blank.
    const isDocx = isDocxFile(record.mimeType, target.recordName, record.recordName, target.extension, record.fileRecord?.extension);
    setPreviewFile({
      id: target.recordId,
      name: target.recordName,
      url: isDocx ? '' : URL.createObjectURL(blob),
      blob: isDocx ? blob : undefined,
      type: resolvedType,
      size: record.sizeInBytes,
      isLoading: false,
      recordDetails,
      webUrl: record.webUrl ?? undefined,
      previewRenderable: record.previewRenderable,
      ...anchor,
    });
  } catch (error) {
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
