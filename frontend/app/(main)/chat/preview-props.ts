import type { ChatPreviewFile } from './store';

/** The chat store's preview file as the file preview's props. */
export function chatPreviewProps(previewFile: ChatPreviewFile) {
  return {
    source: 'chat' as const,
    file: {
      id: previewFile.id,
      name: previewFile.name,
      url: previewFile.url,
      blob: previewFile.blob,
      type: previewFile.type,
      sourceType: previewFile.sourceType,
      size: previewFile.size,
      webUrl: previewFile.webUrl,
      previewRenderable: previewFile.previewRenderable,
      version: previewFile.version,
    },
    isLoading: previewFile.isLoading,
    error: previewFile.error,
    recordDetails: previewFile.recordDetails,
    initialPage: previewFile.initialPage,
    highlightBox: previewFile.highlightBox,
    citations: previewFile.citations,
    initialCitationId: previewFile.initialCitationId,
    jumpSeq: previewFile.jumpSeq,
    hideFileDetails: previewFile.hideFileDetails,
    showDownload: previewFile.showDownload,
    latestVersion: previewFile.latestVersion,
    onVersionChange: previewFile.onVersionChange,
    isSwitchingVersion: previewFile.isSwitchingVersion,
    defaultTab: 'preview' as const,
  };
}
