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
 * 1. Remove content inside brackets [content], (content), {content}
 * 2. Process text word by word, preserving words EXACTLY as they appear (no character removal)
 * 3. When encountering words with problematic special characters, save current segment and start fresh from NEXT word
 * 4. Keep the longest valid segment found (up to maxWords)
 * 5. Return plain text that will be properly URL-encoded by encodeURIComponent
 * @param text - The content text to process
 * @param maxWords - Maximum number of words to include (default: 5)
 * @returns A clean plain text fragment suitable for URL text highlighting
 */
export const extractCleanTextFragment = (text: string, maxWords: number = 5): string => {
  if (!text || typeof text !== 'string') return '';

  // Step 1: Remove content inside brackets - these are usually metadata/notes
  // This preserves the text structure outside brackets
  let cleaned = text
    .replace(/\[[^\]]*\]/g, ' ') // Remove [content]
    .replace(/\([^)]*\)/g, ' ') // Remove (content)
    .replace(/\{[^}]*\}/g, ' '); // Remove {content}

  // Step 2: Normalize whitespace - replace all whitespace sequences with single spaces
  // CRITICAL: This only normalizes whitespace, never modifies actual characters or numbers
  cleaned = cleaned.replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';

  // Step 3: Split into words by whitespace
  // Each word is preserved EXACTLY as it appears - no modification
  const words = cleaned.split(/\s+/).filter((w) => w.length > 0);
  if (words.length === 0) return '';

  // Step 4: Determine if a word is safe for URL text fragments
  // We allow words with common punctuation that works in text fragments:
  // - Letters, numbers, periods, commas, colons, semicolons, hyphens, apostrophes
  // We reject words with characters that break text fragment matching:
  // - @, #, $, %, &, *, +, =, <, >, [, ], {, }, |, \, /, etc.
  const isSafeWord = (word: string): boolean => {
    if (!word || word.length === 0) return false;

    // Must contain at least one alphanumeric character
    if (!/[a-zA-Z0-9]/.test(word)) return false;

    // Check if word contains only safe characters for text fragments
    // Allowed: letters, numbers, periods, commas, colons, semicolons, hyphens, apostrophes, exclamation, question marks
    // These characters work well with URL text fragment highlighting
    const hasOnlySafeChars = /^[a-zA-Z0-9.,:;!?\-']+$/.test(word);

    return hasOnlySafeChars;
  };

  // Step 5: Build valid segments using sliding window approach
  // CRITICAL: We do NOT modify words - we use them exactly as they appear
  // When we encounter a word with problematic characters, we save the current
  // segment and start a new segment from the NEXT word
  let bestSegment: string[] = [];
  let currentSegment: string[] = [];

  // Process each word sequentially, using words EXACTLY as they appear
  for (let i = 0; i < words.length; i += 1) {
    const word = words[i]; // Use word exactly as-is, no cleaning/modification

    // Check if this word is safe for text fragments
    if (isSafeWord(word)) {
      // Add the word to current segment AS-IS (no modification)
      // This preserves: "1:", "Step", "2025", etc. exactly
      currentSegment.push(word);

      // If we've reached max words, this is our best segment - return immediately
      if (currentSegment.length >= maxWords) {
        // Join with single spaces - encodeURIComponent will handle URL encoding
        // Result: "Step 1: Register Application in" (preserves colon in "1:")
        return currentSegment.slice(0, maxWords).join(' ');
      }
    } else {
      // Word contains problematic special characters (e.g., @, #, $, etc.)
      // Save current segment if it's better than what we have
      if (currentSegment.length > bestSegment.length) {
        bestSegment = [...currentSegment]; // Copy array to preserve it
      }
      // CRITICAL: Start fresh with a new segment from the NEXT word
      // This maintains contiguous substrings for proper highlighting
      // We skip the problematic word entirely
      currentSegment = [];
    }
  }

  // After processing all words, check if the last segment is better
  if (currentSegment.length > bestSegment.length) {
    bestSegment = currentSegment;
  }

  // Step 6: Return the best segment found (limit to maxWords)
  if (bestSegment.length === 0) {
    // Fallback: try to get the first safe word
    const firstSafeWord = words.find((word) => isSafeWord(word));
    if (firstSafeWord) {
      return firstSafeWord; // Return single word if that's all we have
    }
    return ''; // No safe words found
  }

  return bestSegment.slice(0, maxWords).join(' ');
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
