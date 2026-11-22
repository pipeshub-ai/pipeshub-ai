'use client';

import { useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useChatStore } from '@/chat/store';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import { openRecordContent, PartyLeftError, SessionEndedError, type Pending, type Variant } from '@/lib/record-content';
import type { CitationData, CitationCallbacks, CitationOrigin, CitationMaps } from './types';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import {
  isPresentationFile,
  isDocxFile,
  isLegacyWordDocFile,
  resolvePreviewMimeAfterStream,
} from '@/app/components/file-preview/utils';

/** Newest load of the chat preview. A load that is no longer current drops its answer. */
let loadToken = 0;
let activePending: Pending | null = null;

/** Which stream the full path would ask for, from the citation's own fields; null when it cannot tell. */
export function variantOfCitation(c: CitationData): Variant | null {
  if (!c.mimeType && !c.extension) return null;
  const name = c.recordName;
  const mime = c.mimeType || c.extension || '';
  return isPresentationFile(mime, name) || isLegacyWordDocFile(mime, name) ? 'pdf' : 'original';
}

function sameCitationSet(a: PreviewCitation[] | undefined, b: PreviewCitation[]): boolean {
  if (!a || a.length !== b.length) return false;
  return a.every((x, i) => x.id === b[i].id && x.content === b[i].content);
}

interface Target {
  initialPage?: number;
  highlightBox?: PreviewCitation['boundingBox'];
  initialCitationId?: string;
  citations: PreviewCitation[];
}

/** The target now shown for this record, or the one the load began with when another record is shown. */
function currentTarget(recordId: string, fallback: Target): Target {
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
 * Hook that provides citation interaction callbacks:
 * - Open in Collection: navigates to the knowledge-base page with the record's KB context
 * - Preview: loads the file and opens the file preview sidebar/fullscreen, navigating
 *   to the cited page for PDFs
 */
export function useCitationActions(): CitationCallbacks {
  const router = useRouter();
  const setPreviewFile = useChatStore((s) => s.setPreviewFile);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);

  /**
   * Open the record in its source context.
   *
   * - CONNECTOR records: open the external webUrl in a new tab
   * - UPLOAD records: navigate to all-records view filtered by the record's KB
   */
  const onOpenInCollection = useCallback(
    async (citation: CitationData) => {
      // Resolve origin — prefer citation-level, fall back to fetching record details
      let origin: CitationOrigin | undefined = citation.origin;

      if (origin === 'CONNECTOR') {
        // External source — open in the original app (OneDrive, Slack, etc.)
        if (citation.webUrl) {
          window.open(citation.webUrl, '_blank', 'noopener,noreferrer');
        } else {
          console.warn('Citation has CONNECTOR origin but no webUrl:', citation.recordId);
        }
        return;
      }

      // UPLOAD origin (or unknown) — navigate to KB's all-records view
      try {
        const recordDetails = await KnowledgeBaseApi.getRecordDetails(citation.recordId);

        // If origin was unknown, resolve it now
        if (!origin) {
          origin = recordDetails.record.origin;
          // Re-check if it's actually a connector record
          if (origin === 'CONNECTOR') {
            const webUrl = recordDetails.record.webUrl;
            if (webUrl) {
              window.open(webUrl, '_blank', 'noopener,noreferrer');
            }
            return;
          }
        }

        const kb = recordDetails.knowledgeBase;
        if (kb) {
          // In all-records mode, collection roots are addressed as app nodes.
          // Using "kb" causes backend validation to fail with Invalid parent_type.
          router.push(`/knowledge-base?view=all-records&nodeType=app&nodeId=${encodeURIComponent(kb.id)}`);
        } else {
          // No KB context — show root-level all-records
          router.push('/knowledge-base?view=all-records');
        }
      } catch (error) {
        console.error('Failed to navigate to collection:', error);
        router.push('/knowledge-base?view=all-records');
      }
    },
    [router],
  );

  /**
   * Open the file preview sidebar for the cited record.
   * For PDFs, navigate to the cited page number.
   * When citationMaps is provided, all citations referencing the same record
   * are extracted and stored so the file-preview CitationsPanel can display them.
   */
  const onPreview = useCallback(
    async (citation: CitationData, citationMaps?: CitationMaps) => {
      const initialPage = citation.pageNum?.[0] ?? 1;
      const highlightBox = citation.boundingBox;

      // Derive preview-level citations for this record from the full map
      const recordCitations: PreviewCitation[] = citationMaps
        ? Object.entries(citationMaps.citationsOrder)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([, citId]) => citationMaps.citations[citId])
            .filter((c) => c && c.recordId === citation.recordId)
            .map((c) => ({
              id: c.citationId,
              content: c.content,
              pageNumbers: c.pageNum,
              paragraphNumbers: c.blockNum,
              boundingBox: c.boundingBox,
            }))
        : [];

      const variant = variantOfCitation(citation);
      const current = useChatStore.getState().previewFile;
      const target = {
        initialPage,
        highlightBox,
        initialCitationId: citation.citationId,
        citations:
          current && sameCitationSet(current.citations, recordCitations)
            ? current.citations
            : recordCitations,
      };
      if (current && current.id === citation.recordId && variant && current.variant === variant && !current.error) {
        // The record is on screen, or its load is running: only the target moves. No request, no remount.
        setPreviewFile({
          ...current,
          ...target,
          jumpSeq: current.isLoading ? current.jumpSeq : (current.jumpSeq ?? 0) + 1,
        });
        return;
      }

      const token = ++loadToken;
      const isCurrent = () => token === loadToken;
      activePending?.leave();
      activePending = null;

      try {
        // 1. Show loading state immediately (replacing a preview releases its lease).
        setPreviewFile({
          id: citation.recordId,
          name: citation.recordName,
          url: '',
          type: citation.mimeType || citation.extension || '',
          isLoading: true,
          variant: variant ?? undefined,
          jumpSeq: 0,
          ...target,
          citations: recordCitations,
        });
        setPreviewMode('sidebar');

        // 2. Fetch details first so non-previewable records skip the stream. This call also
        //    mints the grant that lets a connector record's retained copy be reused.
        const { details: recordDetails, grant } = await KnowledgeBaseApi.getRecordDetailsForOpen(
          citation.recordId,
        );
        if (!isCurrent()) return;
        const record = recordDetails?.record;
        if (!record) {
          throw new Error('Record details unavailable');
        }
        if (record.previewRenderable === false) {
          setPreviewFile({
            id: citation.recordId,
            name: citation.recordName,
            url: '',
            type: record.mimeType || citation.mimeType || citation.extension || '',
            size: record.sizeInBytes,
            isLoading: false,
            recordDetails,
            webUrl: record.webUrl ?? undefined,
            previewRenderable: false,
            variant: variant ?? undefined,
            jumpSeq: 0,
            ...currentTarget(citation.recordId, target),
          });
          return;
        }

        // Prefer fresh record metadata for conversion; citation fields are fallbacks.
        const previewMime = record.mimeType || citation.mimeType;
        const previewName = record.recordName || citation.recordName;
        // PPT/PPTX and legacy Word (.doc) may be converted to PDF server-side for preview.
        const streamAsPdf =
          isPresentationFile(previewMime, previewName) ||
          isLegacyWordDocFile(previewMime, previewName);
        const streamVariant: Variant = streamAsPdf ? 'pdf' : 'original';
        const pending = openRecordContent(citation.recordId, streamVariant, grant ?? undefined);
        activePending = pending;
        const lease = await pending.promise;
        if (!isCurrent()) {
          lease.release();
          return;
        }
        activePending = null;

        const recordMime = record.mimeType || citation.extension || '';
        const resolvedType = resolvePreviewMimeAfterStream(
          recordMime,
          previewName,
          lease.blob,
          streamAsPdf,
        );
        const fr = record.fileRecord;
        const isDocx = isDocxFile(
          record.mimeType,
          citation.recordName,
          record.recordName,
          citation.extension,
          fr?.extension,
        );

        // 3. Land on the target the user chose last, not the one this load started with.
        const latest = useChatStore.getState().previewFile;
        setPreviewFile({
          id: citation.recordId,
          name: citation.recordName,
          url: isDocx ? '' : lease.url,
          blob: isDocx ? lease.blob : undefined,
          lease,
          variant: streamVariant,
          type: resolvedType,
          size: record.sizeInBytes,
          isLoading: false,
          recordDetails,
          webUrl: record.webUrl ?? undefined,
          previewRenderable: record.previewRenderable,
          jumpSeq: (latest?.jumpSeq ?? 0) + 1,
          ...currentTarget(citation.recordId, target),
        });
      } catch (error) {
        if (!isCurrent()) return;
        if (error instanceof PartyLeftError || error instanceof SessionEndedError) return;
        console.error('Failed to load file preview:', error);
        setPreviewFile({
          id: citation.recordId,
          name: citation.recordName,
          url: '',
          type: citation.mimeType || '',
          error: error instanceof Error ? error.message : 'Failed to load file',
          isLoading: false,
        });
      }
    },
    [setPreviewFile, setPreviewMode],
  );

  return useMemo(
    () => ({ onPreview, onOpenInCollection }),
    [onPreview, onOpenInCollection]
  );
}
