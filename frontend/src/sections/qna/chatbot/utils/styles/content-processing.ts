import type { CustomCitation } from 'src/types/chat-bot';

/**
 * Centralized content processing utilities to avoid duplication
 * between StreamingManager and chat message components
 */

export interface ProcessedContent {
  processedContent: string;
  processedCitations: CustomCitation[];
  citationMap: { [key: number]: CustomCitation };
}

/**
 * Build the set of URLs that belong to known citations so we only strip
 * markdown links that actually correspond to a citation chip.
 */
const buildCitationUrlSet = (citations: CustomCitation[]): Set<string> => {
  const urls = new Set<string>();
  citations.forEach((citation) => {
    const webUrl = citation.metadata?.webUrl;
    if (webUrl) urls.add(webUrl);
  });
  return urls;
};

/**
 * Process raw markdown content for better rendering.
 * Strips the URL portion of citation markdown links `[N](url)` to bare `[N]`
 * so the chat renderer can turn them into citation chips. Only strips links
 * whose target is either an internal block URL or matches a known citation URL;
 * unrelated markdown links (e.g. `[2024](https://example.com)`) are preserved.
 */
export const processMarkdownContent = (
  content: string,
  citations: CustomCitation[] = []
): string => {
  if (!content) return '';

  const citationUrls = buildCitationUrlSet(citations);
  const internalBlockUrl = /\/record\/[^)]*?preview[^)]*?blockIndex=\d+/;

  return content
    // Fix escaped newlines
    .replace(/\\n/g, '\n')
    // Strip citation markdown links [N](url) to [N] so they render as citation chips.
    // Only strip when the URL is a known citation target (internal block URL or
    // a URL that appears in the citations list).
    .replace(/\[(\d+)\]\(([^)]+)\)/g, (match, num, url) => {
      const trimmedUrl = url.trim();
      if (internalBlockUrl.test(trimmedUrl) || citationUrls.has(trimmedUrl)) {
        return `[${num}]`;
      }
      return match;
    })
    // Clean up trailing whitespace but preserve structure
    .trim();
};

/**
 * Extract citation numbers from content and build citation mapping
 */
export const extractCitationNumbers = (content: string): Set<number> => {
  const citationMatches = Array.from(content.matchAll(/\[(\d+)\]/g));
  return new Set(citationMatches.map((match) => parseInt(match[1], 10)));
};

/**
 * Build citation mapping from citations array
 */
export const buildCitationMap = (
  citations: CustomCitation[],
  mentionedNumbers?: Set<number>
): { citationMap: { [key: number]: CustomCitation }; processedCitations: CustomCitation[] } => {
  const citationMap: { [key: number]: CustomCitation } = {};
  const processedCitations: CustomCitation[] = [];

  // First, map citations by their chunkIndex if available
  citations.forEach((citation, index) => {
    const citationNumber = citation.chunkIndex || index + 1;
    if (!citationMap[citationNumber]) {
      citationMap[citationNumber] = citation;
      processedCitations.push({
        ...citation,
        chunkIndex: citationNumber,
      });
    }
  });

  // If we have mentioned numbers, ensure we have citations for all of them
  if (mentionedNumbers) {
    mentionedNumbers.forEach((num) => {
      if (!citationMap[num] && citations[num - 1]) {
        citationMap[num] = citations[num - 1];
        if (!processedCitations.some(c => c === citations[num - 1])) {
          processedCitations.push({
            ...citations[num - 1],
            chunkIndex: num,
          });
        }
      }
    });
  }

  // Sort citations by chunkIndex
  const sortedCitations = processedCitations.sort(
    (a, b) => (a.chunkIndex || 0) - (b.chunkIndex || 0)
  );

  return {
    citationMap,
    processedCitations: sortedCitations,
  };
};

/**
 * Main function to process content and citations together
 * This is the unified function that should be used everywhere
 */
export const processStreamingContent = (
  rawContent: string,
  citations: CustomCitation[] = []
): ProcessedContent => {
  if (!rawContent) {
    return {
      processedContent: '',
      processedCitations: citations,
      citationMap: {},
    };
  }

  // Process the markdown content — pass citations so only known citation
  // links get stripped; unrelated markdown links stay intact.
  const processedContent = processMarkdownContent(rawContent, citations);

  // Extract citation numbers from content
  const mentionedNumbers = extractCitationNumbers(processedContent);

  // Build citation mapping
  const { citationMap, processedCitations } = buildCitationMap(citations, mentionedNumbers);

  return {
    processedContent,
    processedCitations,
    citationMap,
  };
};

/**
 * Legacy function for backward compatibility with existing StreamingManager
 * This can be used to replace the existing processStreamingContent method
 */
export const processStreamingContentLegacy = (
  rawContent: string,
  citations: CustomCitation[] = []
): {
  processedContent: string;
  processedCitations: CustomCitation[];
} => {
  const result = processStreamingContent(rawContent, citations);
  return {
    processedContent: result.processedContent,
    processedCitations: result.processedCitations,
  };
};

/**
 * Utility function specifically for chat message rendering
 * Returns the same data structure as the original extractAndProcessCitations
 */
export const extractAndProcessCitations = (
  content: string,
  streamingCitations: CustomCitation[] = []
): {
  processedContent: string;
  citations: CustomCitation[];
  citationMap: { [key: number]: CustomCitation };
} => {
  const result = processStreamingContent(content, streamingCitations);
  return {
    processedContent: result.processedContent,
    citations: result.processedCitations,
    citationMap: result.citationMap,
  };
};