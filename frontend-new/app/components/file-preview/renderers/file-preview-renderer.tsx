'use client';

import dynamic from 'next/dynamic';
import { ImageRenderer } from './image-renderer';
import { TextRenderer } from './text-renderer';
import { MarkdownRenderer } from './markdown-renderer';
import { MediaRenderer } from './media-renderer';
import { SpreadsheetRenderer } from './spreadsheet-renderer';
import { DocxRenderer } from './docx-renderer';
import { HtmlRenderer } from './html-renderer';
import { DocumentPreview, UnknownPreview } from './index';

// react-pdf-highlighter bundles pdfjs-dist which accesses `document` at module
// init — it must be excluded from the server bundle with ssr:false.
// PDFRenderer is intentionally NOT re-exported from ./index for the same reason.
const PDFRenderer = dynamic(() => import('./pdf-renderer').then((m) => m.PDFRenderer), { ssr: false });
import { getRendererType } from '../utils';
import type { FilePreviewRendererProps } from '../types';

export function FilePreviewRenderer({ fileUrl, fileName, fileType, pagination, highlightBox, highlightPage, citations, activeCitationId, onHighlightClick }: FilePreviewRendererProps) {
  const rendererType = getRendererType(fileType || '', fileName);

  switch (rendererType) {
    case 'pdf':
      // PDFs support pagination
      return <PDFRenderer fileUrl={fileUrl} fileName={fileName} pagination={pagination} highlightBox={highlightBox} highlightPage={highlightPage} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'image':
      return <ImageRenderer fileUrl={fileUrl} fileName={fileName} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'markdown':
      return <MarkdownRenderer fileUrl={fileUrl} fileName={fileName} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'html':
      return <HtmlRenderer fileUrl={fileUrl} fileName={fileName} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'text':
      // Text files could support chunked pagination in the future
      return <TextRenderer fileUrl={fileUrl} fileName={fileName} fileType={fileType} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'media':
      return <MediaRenderer fileUrl={fileUrl} fileName={fileName} fileType={fileType || ''} />;
    case 'spreadsheet':
      return <SpreadsheetRenderer fileUrl={fileUrl} fileName={fileName} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'docx':
      return <DocxRenderer fileUrl={fileUrl} fileName={fileName} citations={citations} activeCitationId={activeCitationId} onHighlightClick={onHighlightClick} />;
    case 'document':
      return <DocumentPreview fileUrl={fileUrl} fileName={fileName} fileType={fileType} />;
    default:
      return <UnknownPreview fileUrl={fileUrl} fileName={fileName} fileType={fileType} />;
  }
}
