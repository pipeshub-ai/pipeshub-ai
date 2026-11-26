'use client';

import { useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useChatStore } from '@/chat/store';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import type { CitationData, CitationCallbacks, CitationOrigin, CitationMaps } from './types';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import { loadRecordPreview } from '@/chat/record-preview';

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

      await loadRecordPreview(
        {
          recordId: citation.recordId,
          recordName: citation.recordName,
          mimeType: citation.mimeType,
          extension: citation.extension,
          initialPage,
          highlightBox,
          citations: recordCitations,
          initialCitationId: citation.citationId,
        },
        { setPreviewFile, setPreviewMode },
      );
    },
    [setPreviewFile, setPreviewMode],
  );

  return useMemo(
    () => ({ onPreview, onOpenInCollection }),
    [onPreview, onOpenInCollection]
  );
}
