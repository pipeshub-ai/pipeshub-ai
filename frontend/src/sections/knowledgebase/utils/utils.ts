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
      .replace(/\([^)]*\)/g, ' ')  // Remove (content)
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
  