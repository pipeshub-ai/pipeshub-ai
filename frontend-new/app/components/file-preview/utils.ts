'use client';

import type { FileType, FilePreviewSource, TabConfig, PaginationVisibility } from './types';

/**
 * Get file type category from file extension or mime type
 */
export function getFileType(fileName: string, _mimeType?: string): FileType {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  
  // Images
  if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) {
    return 'image';
  }
  
  // PDFs
  if (ext === 'pdf') {
    return 'pdf';
  }
  
  // Documents
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) {
    return 'document';
  }
  
  // Spreadsheets
  if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) {
    return 'spreadsheet';
  }
  
  // Presentations
  if (['ppt', 'pptx', 'odp'].includes(ext)) {
    return 'presentation';
  }
  
  // Code files
  if (['js', 'ts', 'jsx', 'tsx', 'py', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'php', 'html', 'css', 'json', 'xml', 'yaml', 'yml', 'md'].includes(ext)) {
    return 'code';
  }
  
  // Text files
  if (['txt', 'log'].includes(ext)) {
    return 'text';
  }
  
  return 'unknown';
}

/**
 * Get tab configuration based on source
 */
export function getTabsForSource(_source: FilePreviewSource): TabConfig[] {
  return [
    { id: 'preview', label: 'Preview', visible: true },
    { id: 'file-details', label: 'File Details', visible: true },
  ];
}

/**
 * Format file size in human readable format
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Bytes';
  
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
}

/**
 * Format date string
 */
export function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date);
}

/**
 * Determine renderer type based on MIME type and file extension
 */
export function getRendererType(mimeType: string, fileName: string): string {
  // Prefer MIME type over extension
  if (mimeType) {
    if (mimeType === 'application/pdf') return 'pdf';
    if (mimeType.startsWith('image/')) return 'image';
    if (mimeType.startsWith('video/')) return 'media';
    if (mimeType.startsWith('audio/')) return 'media';
    if (mimeType === 'text/markdown') return 'markdown';
    if (mimeType === 'text/html') return 'html'; // Rendered in sandboxed iframe, not as source code
    // Spreadsheets
    if (mimeType === 'text/csv') return 'spreadsheet';
    if (mimeType === 'application/vnd.ms-excel') return 'spreadsheet';
    if (mimeType === 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') return 'spreadsheet';
    // Word documents (docx only)
    if (mimeType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document') return 'docx';
    // PowerPoint -> PDF (backend converts)
    if (mimeType === 'application/vnd.ms-powerpoint') return 'pdf';
    if (mimeType === 'application/vnd.openxmlformats-officedocument.presentationml.presentation') return 'pdf';
    if (mimeType.startsWith('text/')) return 'text';
  }

  // Fallback to extension
  const ext = fileName.split('.').pop()?.toLowerCase() || '';

  // Images
  if (['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp', '.ico'].includes(`.${ext}`)) {
    return 'image';
  }

  // PDF
  if (ext === 'pdf') return 'pdf';

  // Markdown
  if (['.md', '.markdown', '.mdx'].includes(`.${ext}`)) return 'markdown';

  // HTML — rendered in sandboxed iframe via HtmlRenderer (not shown as source code)
  if (['.html', '.htm'].includes(`.${ext}`)) return 'html';

  // Code/Text
  if (['.txt', '.log', '.js', '.jsx', '.ts', '.tsx', '.py', '.java', '.c', '.cpp', '.h', '.hpp',
       '.cs', '.php', '.rb', '.go', '.rs', '.swift', '.kt', '.sh', '.bash', '.sql', '.yml', '.yaml',
       '.json', '.xml', '.css', '.scss', '.sass', '.less'].includes(`.${ext}`)) {
    return 'text';
  }

  // Video
  if (['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv'].includes(`.${ext}`)) {
    return 'media';
  }

  // Audio
  if (['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'].includes(`.${ext}`)) {
    return 'media';
  }

  // Spreadsheets
  if (['.xls', '.xlsx', '.csv'].includes(`.${ext}`)) return 'spreadsheet';

  // Word documents
  if (ext === 'docx') return 'docx';
  if (ext === 'doc') return 'document'; // Legacy binary format - download only

  // PowerPoint -> PDF (backend converts)
  if (['.ppt', '.pptx'].includes(`.${ext}`)) return 'pdf';

  return 'unknown';
}


/**
 * Determine if pagination controls should be visible
 */
export function shouldShowPagination(
  fileType: string,
  fileName: string,
  totalPages: number | null,
  isLoading: boolean,
  hasError: boolean
): PaginationVisibility {
  // Don't show during loading or error states
  if (isLoading) return { shouldShow: false, reason: 'loading' };
  if (hasError) return { shouldShow: false, reason: 'error' };

  // Only PDFs support pagination currently
  const rendererType = getRendererType(fileType, fileName);
  if (rendererType !== 'pdf') {
    return { shouldShow: false, reason: 'unsupported' };
  }

  // Show pagination for all PDFs (including single-page, to show "1 / 1")
  return { shouldShow: true };
}
