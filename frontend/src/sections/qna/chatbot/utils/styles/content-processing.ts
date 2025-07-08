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
 * Process raw markdown content for better rendering
 */
export const processMarkdownContent = (content: string): string => {
  if (!content) return '';

  return content
    // Fix escaped newlines
    .replace(/\\n/g, '\n')
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

  // Process the markdown content
  const processedContent = processMarkdownContent(rawContent);

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