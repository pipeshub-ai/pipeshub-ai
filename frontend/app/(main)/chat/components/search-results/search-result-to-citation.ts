import type { SearchResultItem } from '@/chat/types';
import type { CitationData, CitationOrigin } from '../message-area/response-tabs/citations/types';

/**
 * Maps a keyword-search result to the normalized `CitationData` shape so it can
 * be fed into `useCitationActions().onPreview`, reusing the same file-preview
 * flow the Chat citations use. Search results have no real citation id, so one
 * is synthesized from the record + block indices (unique per result on the page).
 */
export function searchResultToCitationData(result: SearchResultItem): CitationData {
  const { metadata } = result;

  return {
    citationId: `search-${metadata.recordId}-${result.block_index ?? 0}`,
    content: result.content,
    chunkIndex: result.block_index ?? 0,
    recordId: metadata.recordId,
    recordName: metadata.recordName,
    connector: metadata.connector,
    recordType: metadata.recordType,
    webUrl: metadata.webUrl,
    mimeType: metadata.mimeType ?? '',
    extension: metadata.extension ?? '',
    pageNum: metadata.pageNum?.filter((p): p is number => p !== null),
    blockNum: metadata.blockNum?.filter((b): b is number => b !== null),
    previewRenderable: metadata.previewRenderable ?? true,
    hideWeburl: metadata.hideWeburl ?? false,
    citationType: result.citationType,
    origin: metadata.origin as CitationOrigin | undefined,
    boundingBox: metadata.bounding_box,
  };
}
