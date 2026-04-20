'use client';

import { Flex, Text } from '@radix-ui/themes';
import type { FilePreviewRendererProps } from '../types';

// Export new specialized renderers
// NOTE: PDFRenderer is NOT exported here — it must be dynamically imported
// (via next/dynamic + ssr:false) to keep pdfjs-dist out of the server bundle.
export { ImageRenderer } from './image-renderer';
export { TextRenderer } from './text-renderer';
export { MarkdownRenderer } from './markdown-renderer';
export { MediaRenderer } from './media-renderer';
export { SpreadsheetRenderer } from './spreadsheet-renderer';
export { DocxRenderer } from './docx-renderer';

// Fallback renderer for unsupported Office documents
export function DocumentPreview({ fileUrl, fileName }: FilePreviewRendererProps) {
  // For Office documents (Word, Excel, PowerPoint)
  // No good web rendering library - offer download
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="4"
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: 'var(--slate-2)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-6)',
      }}
    >
      <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--slate-9)' }}>
        description
      </span>
      <Text size="4" weight="medium" style={{ textAlign: 'center' }}>
        {fileName}
      </Text>
      <Text size="2" color="gray" style={{ textAlign: 'center', maxWidth: '400px' }}>
        Preview not available for this document type. Please download to view.
      </Text>
      {fileUrl && fileUrl.trim() !== '' && (
        <a
          href={fileUrl}
          download={fileName}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            padding: 'var(--space-2) var(--space-4)',
            backgroundColor: 'var(--accent-9)',
            color: 'white',
            borderRadius: 'var(--radius-2)',
            textDecoration: 'none',
            fontSize: 'var(--font-size-2)',
            fontWeight: 500,
            transition: 'background-color 0.15s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--accent-10)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--accent-9)';
          }}
        >
          <span className="material-icons-outlined" style={{ fontSize: '18px' }}>
            download
          </span>
          Download File
        </a>
      )}
    </Flex>
  );
}

export function UnknownPreview({ fileName, fileUrl }: FilePreviewRendererProps) {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="4"
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: 'var(--slate-2)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-6)',
      }}
    >
      <span className="material-icons-outlined" style={{ fontSize: '48px', color: 'var(--slate-9)' }}>
        insert_drive_file
      </span>
      <Text size="4" weight="medium" style={{ textAlign: 'center' }}>
        {fileName}
      </Text>
      <Text size="2" color="gray" style={{ textAlign: 'center', maxWidth: '400px' }}>
        Preview not available for this file type
      </Text>
      {fileUrl && fileUrl.trim() !== '' && (
        <a
          href={fileUrl}
          download={fileName}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            padding: 'var(--space-2) var(--space-4)',
            backgroundColor: 'var(--accent-9)',
            color: 'white',
            borderRadius: 'var(--radius-2)',
            textDecoration: 'none',
            fontSize: 'var(--font-size-2)',
            fontWeight: 500,
            transition: 'background-color 0.15s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--accent-10)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--accent-9)';
          }}
        >
          <span className="material-icons-outlined" style={{ fontSize: '18px' }}>
            download
          </span>
          Download File
        </a>
      )}
    </Flex>
  );
}
