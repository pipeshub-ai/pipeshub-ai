'use client';

import { useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useChatStore } from '@/chat/store';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import type { CitationData, CitationCallbacks, CitationOrigin, CitationMaps } from './types';
import type { PreviewCitation } from '@/app/components/file-preview/types';

/** MIME types for PowerPoint presentation files (.ppt, .pptx) */
const PPT_MIME_TYPES = [
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
];

/**
 * Checks whether a file is a PowerPoint presentation (PPT/PPTX) by MIME type or extension.
 * PPT/PPTX files require server-side conversion to PDF via the `convertTo=pdf` query param
 * on the streaming API before they can be previewed in the browser.
 */
function isPresentationFile(mimeType?: string, fileName?: string): boolean {
  if (mimeType && PPT_MIME_TYPES.includes(mimeType)) return true;
  if (fileName) {
    const ext = fileName.split('.').pop()?.toLowerCase();
    if (ext === 'ppt' || ext === 'pptx') return true;
  }
  return false;
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
          // Navigate to all-records drilled into this KB
          router.push(`/knowledge-base?view=all-records&nodeType=kb&nodeId=${kb.id}`);
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

      try {
        // 1. Show loading state immediately
        setPreviewFile({
          id: citation.recordId,
          name: citation.recordName,
          url: '',
          type: citation.mimeType || citation.extension || '',
          isLoading: true,
          initialPage,
          highlightBox,
          citations: recordCitations,
          initialCitationId: citation.citationId,
        });
        setPreviewMode('sidebar');

        // 2. Fetch record details and stream file in parallel
        // PPT/PPTX files need server-side conversion to PDF for browser preview
        const streamOptions = isPresentationFile(citation.mimeType, citation.recordName) ? { convertTo: 'application/pdf' } : undefined;
        const [recordDetails, blob] = await Promise.all([
          KnowledgeBaseApi.getRecordDetails(citation.recordId),
          KnowledgeBaseApi.streamRecord(citation.recordId, streamOptions),
        ]);

        // 3. Create object URL from blob
        const url = URL.createObjectURL(blob);

        // 4. Update state with actual file URL and record details
        setPreviewFile({
          id: citation.recordId,
          name: citation.recordName,
          url,
          type: recordDetails.record.mimeType || citation.extension || '',
          size: recordDetails.record.sizeInBytes,
          isLoading: false,
          recordDetails,
          initialPage,
          highlightBox,
          citations: recordCitations,
          initialCitationId: citation.citationId,
        });
      } catch (error) {
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
