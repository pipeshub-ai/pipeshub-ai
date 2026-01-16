import type { Icon as IconifyIcon } from '@iconify/react';
import React from 'react';
import descriptionIcon from '@iconify-icons/mdi/file-document-outline';
import pdfIcon from '@iconify-icons/vscode-icons/file-type-pdf2';
import docIcon from '@iconify-icons/vscode-icons/file-type-word';
import xlsIcon from '@iconify-icons/vscode-icons/file-type-excel';
import pptIcon from '@iconify-icons/vscode-icons/file-type-powerpoint';
import txtIcon from '@iconify-icons/vscode-icons/file-type-text';
import mdIcon from '@iconify-icons/vscode-icons/file-type-markdown';
import htmlIcon from '@iconify-icons/vscode-icons/file-type-html';
import jsonIcon from '@iconify-icons/vscode-icons/file-type-json';
import zipIcon from '@iconify-icons/vscode-icons/file-type-zip';
import imageIcon from '@iconify-icons/vscode-icons/file-type-image';
import databaseIcon from '@iconify-icons/mdi/database';

export const getIndexingStatusColor = (
  status: string
): 'success' | 'info' | 'error' | 'warning' | 'default' => {
  switch (status) {
    case 'COMPLETED':
      return 'success';
    case 'IN_PROGRESS':
      return 'info';
    case 'FAILED':
      return 'error';
    case 'NOT_STARTED':
      return 'warning';
    case 'PAUSED':
      return 'warning';
    case 'QUEUED':
      return 'info';
    case 'FILE_TYPE_NOT_SUPPORTED':
      return 'default';
    case 'AUTO_INDEX_OFF':
      return 'default';
    case 'EMPTY':
      return 'info';
    case 'ENABLE_MULTIMODAL_MODELS':
      return 'info';
    default:
      return 'warning';
  }
};

// Helper function to format file size
export const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / k ** i).toFixed(2))} ${sizes[i]}`;
};

// Get file icon based on extension
export const getFileIcon = (
  extension: string
): React.ComponentProps<typeof IconifyIcon>['icon'] => {
  const ext = extension?.toLowerCase() || '';

  switch (ext) {
    case 'pdf':
      return pdfIcon;
    case 'doc':
    case 'docx':
      return docIcon;
    case 'xls':
    case 'xlsx':
    case 'csv':
      return xlsIcon;
    case 'ppt':
    case 'pptx':
      return pptIcon;
    case 'jpg':
    case 'jpeg':
    case 'png':
    case 'gif':
    case 'bmp':
    case 'tiff':
    case 'ico':
    case 'webp':
      return imageIcon;
    case 'zip':
    case 'rar':
    case '7z':
      return zipIcon;
    case 'txt':
      return txtIcon;
    case 'html':
    case 'css':
    case 'js':
      return htmlIcon;
    case 'md':
    case 'mdx':
      return mdIcon;
    case 'json':
      return jsonIcon;
    case 'database':
      return databaseIcon;
    default:
      return descriptionIcon;
  }
};

// Get file icon color based on extension
export const getFileIconColor = (extension: string): string => {
  const ext = extension?.toLowerCase() || '';

  switch (ext) {
    case 'pdf':
      return '#f44336';
    case 'doc':
    case 'docx':
      return '#2196f3';
    case 'xls':
    case 'xlsx':
      return '#4caf50';
    case 'ppt':
    case 'pptx':
      return '#ff9800';
    default:
      return '#1976d2';
  }
};

/**
 * Extracts a clean text fragment for URL text highlighting.
 *
 * Strategy:
 * 1. Remove ONLY content inside square brackets [content] and curly braces {content}
 * 2. Keep content inside parentheses () as they're often part of the actual text
 * 3. Remove leading punctuation (bullets, dashes, etc.)
 * 4. Stop at problematic characters that break Text Fragment API matching (@, #, $, %, &, *, +, =, <, >, |, \, /, ^, ~)
 * 5. Keep safe punctuation (commas, periods, colons, semicolons, hyphens, apostrophes, parentheses, exclamation, question marks)
 * 6. Limit to 1000 characters to prevent excessively long URLs (truncates at last space to avoid cutting words)
 * @param text - The content text to process
 * @returns A clean plain text fragment suitable for URL text highlighting (max 300 characters)
 */
export const extractCleanTextFragment = (text: string): string => {
  if (!text || typeof text !== 'string') return '';

  // Step 1: Remove content inside square brackets and curly braces - these are usually metadata/notes
  // Keep parentheses () as they're often part of the actual content
  let cleaned = text
    .replace(/\[[^\]]*\]/g, ' ') // Remove [content]
    .replace(/\{[^}]*\}/g, ' '); // Remove {content}

  // Step 2: Normalize whitespace - replace all whitespace sequences with single spaces
  cleaned = cleaned.replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';

  // Step 3: Remove leading punctuation/special characters (like leading dashes, bullets, etc.)
  // Keep alphanumeric start and preserve the rest of the text including opening parentheses
  cleaned = cleaned.replace(/^[^\w\s(]+\s*/, '');
  cleaned = cleaned.trim();
  if (!cleaned) return '';

  // Step 4: Find the position of the first problematic character that breaks Text Fragment API
  // Problematic chars: @, #, $, %, &, *, +, =, <, >, |, \, /, ^, ~, [, ], {, }
  // Safe chars: letters, numbers, spaces, commas, periods, colons, semicolons, hyphens, apostrophes, parentheses, !, ?
  const problematicCharPattern = /[@#$%&*+=<>|\\/^~[\]{}]/;
  const match = cleaned.match(problematicCharPattern);
  
  if (match && match.index !== undefined) {
    // Found a problematic character - truncate at that position
    cleaned = cleaned.substring(0, match.index).trim();
  }

  // Step 5: Limit length to prevent excessively long URLs (browser limits ~2000 chars, but be conservative)
  // Truncate at last space within limit to avoid cutting words in half
  const MAX_FRAGMENT_LENGTH = 1000;
  if (cleaned.length > MAX_FRAGMENT_LENGTH) {
    const truncated = cleaned.substring(0, MAX_FRAGMENT_LENGTH);
    const lastSpaceIndex = truncated.lastIndexOf(' ');
    // If we found a space, truncate there; otherwise use the hard limit
    cleaned = lastSpaceIndex > 0 ? truncated.substring(0, lastSpaceIndex).trim() : truncated.trim();
  }

  return cleaned;
};

/**
 * Adds a text fragment to a URL for browser text highlighting.
 *
 * This function handles URL text fragments (Text Fragments API) which allow
 * highlighting specific text on a page when the URL is opened.
 *
 * @param webUrl - The URL to add the text fragment to (can be absolute or relative)
 * @param textFragment - The text fragment to highlight (will be URL-encoded)
 * @returns The URL with the text fragment appended, or the original URL if textFragment is empty
 *
 * @example
 * addTextFragmentToUrl('https://example.com/page', 'Step 1: Register')
 * // Returns: 'https://example.com/page#:~:text=Step%201%3A%20Register'
 *
 * @example
 * addTextFragmentToUrl('https://example.com/page#section', 'Step 1: Register')
 * // Returns: 'https://example.com/page#section:~:text=Step%201%3A%20Register'
 */
export const addTextFragmentToUrl = (webUrl: string, textFragment: string): string => {
  if (!textFragment || !webUrl) {
    return webUrl;
  }

  const encodedFragment = encodeURIComponent(textFragment);

  try {
    // Try to use URL constructor for proper URL handling (works for absolute URLs)
    const url = new URL(webUrl);
    if (url.hash.includes(':~:')) {
      // If text fragment directive already exists, append with &
      url.hash += `&text=${encodedFragment}`;
    } else {
      // Otherwise, add the text fragment directive
      url.hash += `:~:text=${encodedFragment}`;
    }
    return url.toString();
  } catch (e) {
    // Fallback for cases where webUrl might not be a full URL (e.g., relative paths)
    // Handle all three cases: existing :~:, existing #, or no hash
    if (webUrl.includes(':~:')) {
      // Text fragment directive already exists, append with &
      return `${webUrl}&text=${encodedFragment}`;
    }
    if (webUrl.includes('#')) {
      // Hash exists but no text fragment directive, append :~:text=
      return `${webUrl}:~:text=${encodedFragment}`;
    }
    // No hash exists, add #:~:text=
    return `${webUrl}#:~:text=${encodedFragment}`;
  }
};

const mimeTypeToExtensionMap: Record<string, string> = {
  'application/pdf': 'pdf',
  'application/msword': 'doc',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
  'application/vnd.google-apps.spreadsheet': 'xlsx',
  'application/vnd.google-apps.document': 'docx',
  'application/vnd.google-apps.presentation': 'pptx',
  'text/plain': 'txt',
  'text/html': 'html',
  'text/markdown': 'md',
  'text/mdx': 'mdx',
  'text/csv': 'csv',
  'text/tab-separated-values': 'tsv',
  'application/json': 'json',
  'application/xml': 'xml',
  'application/zip': 'zip',
  'application/x-rar-compressed': 'rar',
  'application/x-7z-compressed': '7z',
  'application/x-tar': 'tar',
  'application/x-sh': 'sh',
  'application/x-msdownload': 'exe',
  'application/x-python-code': 'py',
  'application/x-java-archive': 'jar',
  'application/x-genesis-32x-rom': 'genesis',
  'application/x-icns': 'icns',
  'image/svg+xml': 'svg',
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/gif': 'gif',
  'image/bmp': 'bmp',
  'image/tiff': 'tiff',
  'image/ico': 'ico',
  'image/webp': 'webp',
  'image/heic': 'heic',
  'image/heif': 'heif',
  'image/avif': 'avif',
  'image/jxr': 'jxr',
  'image/icns': 'icns',
};

export const getExtensionFromMimeType = (mimeType: string): string =>
  mimeTypeToExtensionMap[mimeType] || '';

/**
 * Gets a web URL with text fragment for highlighting.
 * 
 * This function handles different input types:
 * - CustomCitation: citation with metadata.webUrl, metadata.origin, metadata.blockText
 * - Record + Citation: record with webUrl/origin, and citation with metadata.blockText
 * 
 * @param citationOrRecord - Either a CustomCitation or a PipesHub.Record
 * @param recordCitation - Optional SearchResult when first param is a Record
 * @returns URL with text fragment, or null if URL not available
 */
export const getWebUrlWithFragment = (
  citationOrRecord?: { metadata?: { webUrl?: string; origin?: string; blockText?: string; hideWeburl?: boolean } } | { webUrl?: string; origin?: string; hideWeburl?: boolean },
  recordCitation?: { metadata?: { blockText?: string; webUrl?: string; hideWeburl?: boolean }; content?: string } | { content?: string }
): string | null => {
  let webUrl: string | undefined;
  let origin: string | undefined;
  let blockText: string | undefined;
  let content: string | undefined;
  let hideWeburl: boolean | undefined;

  // Handle CustomCitation type (has metadata property)
  if (citationOrRecord && 'metadata' in citationOrRecord) {
    const citation = citationOrRecord as { metadata?: { webUrl?: string; origin?: string; blockText?: string; hideWeburl?: boolean } };
    webUrl = citation.metadata?.webUrl;
    origin = citation.metadata?.origin;
    blockText = citation.metadata?.blockText;
    hideWeburl = citation.metadata?.hideWeburl;
  } 
  // Handle PipesHub.Record type (has webUrl directly)
  else if (citationOrRecord && 'webUrl' in citationOrRecord) {
    const record = citationOrRecord as { webUrl?: string; origin?: string; hideWeburl?: boolean; };
    webUrl = record.webUrl;
    origin = record.origin;
    // Handle both hideWeburl
    hideWeburl = record.hideWeburl;
    
    // Get blockText from recordCitation if provided
    if (recordCitation) {
      if ('metadata' in recordCitation) {
        blockText = recordCitation.metadata?.blockText;
        // SearchResult has both metadata and content
        if ('content' in recordCitation) {
          content = recordCitation.content;
        }
        // Fallback to recordCitation metadata webUrl if record doesn't have one
        if (!webUrl) {
          webUrl = recordCitation.metadata?.webUrl;
        }
        // Check hideWeburl in recordCitation metadata as well
        if (hideWeburl === undefined) {
          hideWeburl = recordCitation.metadata?.hideWeburl;
        }
      } else if ('content' in recordCitation) {
        content = recordCitation.content;
      }
    }
  }

  // Return null early if hideWeburl is true
  if (hideWeburl === true) {
    return null;
  }

  if (!webUrl) {
    return null;
  }

  try {
    // Handle relative URLs for UPLOAD origin
    if (origin === 'UPLOAD' && webUrl && !webUrl.startsWith('http')) {
      const baseUrl = `${window.location.protocol}//${window.location.host}`;
      webUrl = baseUrl + webUrl;
    }

    // Check if blockText exists and is not empty before adding text fragment
    let textForFragment: string | undefined;
    if (blockText && typeof blockText === 'string' && blockText.trim().length > 0) {
      textForFragment = blockText;
    } else if (content && typeof content === 'string' && content.trim().length > 0) {
      textForFragment = content;
    }

    if (textForFragment) {
      const textFragment = extractCleanTextFragment(textForFragment);
      if (textFragment) {
        return addTextFragmentToUrl(webUrl, textFragment);
      }
    }

    return webUrl;
  } catch (error) {
    console.warn('Error accessing webUrl:', error);
    return null;
  }
};