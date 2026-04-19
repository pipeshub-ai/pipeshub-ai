'use client';

import type { RecordDetailsResponse } from '@/app/(main)/knowledge-base/types';

/**
 * Source types that determine which tabs are available
 */
export type FilePreviewSource = 'collections' | 'all-records' | 'agents' | 'chat';

/**
 * Tab identifiers
 */
export type FilePreviewTab = 'preview' | 'file-details';

/**
 * File type categories for determining preview renderer
 */
export type FileType = 
  | 'pdf'
  | 'image'
  | 'text'
  | 'document' // .doc, .docx
  | 'spreadsheet' // .xls, .xlsx
  | 'presentation' // .ppt, .pptx
  | 'code'
  | 'unknown';

/**
 * File details data structure
 */
export interface FileDetails {
  id: string;
  name: string;
  type: string; // MIME type
  url: string; // Blob URL or streaming URL
  size?: number;
  isLoading?: boolean; // Loading state
  error?: string; // Error message if loading failed
}

/**
 * Tab configuration based on source
 */
export interface TabConfig {
  id: FilePreviewTab;
  label: string;
  visible: boolean;
}

/**
 * Citation data for the preview citations panel.
 * A simplified, source-agnostic shape used by the file-preview layer.
 */
export interface PreviewCitation {
  /** Unique identifier */
  id: string;
  /** The cited text content / snippet */
  content: string;
  /** Page numbers where the citation appears */
  pageNumbers?: number[];
  /** Block / paragraph numbers */
  paragraphNumbers?: number[];
  /** Bounding box for highlighting the cited region (normalized 0-1 coordinates) */
  boundingBox?: Array<{ x: number; y: number }>;
}

/**
 * Props for file preview components
 */
export interface FilePreviewProps {
  /** Whether the sidebar is open */
  open?: boolean;
  
  /** Source context (determines which tabs are shown) */
  source: FilePreviewSource;
  
  /** File to preview */
  file: {
    id: string;
    name: string;
    url: string;
    type: string;
    size?: number;
  };
  
  /** Initially active tab */
  defaultTab?: FilePreviewTab;
  
  /** Callback when sidebar open state changes */
  onOpenChange?: (open: boolean) => void;

  /** Callback when fullscreen preview closes */
  onClose?: () => void;

  /** Callback when fullscreen toggle button is clicked */
  onToggleFullscreen?: () => void;

  /** Loading state */
  isLoading?: boolean;
  
  /** Record details from API */
  recordDetails?: RecordDetailsResponse;

  /** Initial page to navigate to (e.g. from citation pageNum) */
  initialPage?: number;

  /** Bounding box for highlighting in PDF (normalized 0-1 coordinates) */
  highlightBox?: Array<{ x: number; y: number }>;

  /** Citations to display in the citations panel (omit or empty to hide panel) */
  citations?: PreviewCitation[];

  /**
   * Id of the citation the user clicked to open this preview.
   * Seeds the active citation in the panel so clicking `[2]` highlights
   * citation [2] even when multiple citations share the same page.
   */
  initialCitationId?: string;
}

/**
 * Pagination control interface
 */
export interface PaginationControls {
  currentPage: number;
  totalPages: number | null; // null during detection
  onPageChange: (page: number) => void;
  onTotalPagesDetected?: (totalPages: number) => void;
}

/**
 * Pagination visibility state
 */
export interface PaginationVisibility {
  shouldShow: boolean;
  reason?: 'loading' | 'single-page' | 'error' | 'unsupported';
}

/**
 * Props for preview renderers
 */
export interface FilePreviewRendererProps {
  fileUrl: string;
  fileName: string;
  fileType: string;
  pagination?: PaginationControls;
  /** Bounding box for highlighting in PDF (normalized 0-1 coordinates) */
  highlightBox?: Array<{ x: number; y: number }>;
  /** Page number on which to show the highlight overlay */
  highlightPage?: number;
  /** Citations for text-based highlighting in non-PDF renderers */
  citations?: PreviewCitation[];
  /** Currently active citation ID (for scroll-to / active styling) */
  activeCitationId?: string | null;
  /** Called when user clicks a highlight span in the rendered content */
  onHighlightClick?: (citationId: string) => void;
}

/**
 * Props for PDF renderer
 */
export interface PDFRendererProps {
  fileUrl: string;
  fileName: string;
  pagination?: PaginationControls;
  /** Bounding box for highlighting in PDF (normalized 0-1 coordinates) */
  highlightBox?: Array<{ x: number; y: number }>;
  /** Page number on which to show the highlight overlay */
  highlightPage?: number;
  /** Citations for highlighting in the PDF */
  citations?: PreviewCitation[];
  /** Currently active citation ID */
  activeCitationId?: string | null;
  /** Called when user clicks a highlight in the PDF */
  onHighlightClick?: (citationId: string) => void;
}

/**
 * Props for text renderer
 */
export interface TextRendererProps {
  fileUrl: string;
  fileName: string;
  fileType?: string;
  pagination?: PaginationControls;
}
