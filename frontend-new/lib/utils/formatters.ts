/**
 * Format file size in bytes to human readable format
 */
export function formatSize(bytes?: number): string {
  if (!bytes) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Format date string to localized format (e.g., "3 Nov 2025")
 */
export function formatDate(dateString: string | number): string {
  const date = new Date(dateString);
  const day = date.getDate();
  const month = date.toLocaleDateString('en-US', { month: 'short' });
  const year = date.getFullYear();
  return `${day} ${month} ${year}`;
}

/**
 * Format a conversation's date for display as a subtitle in the chat search view.
 *
 * Logic:
 *  - If updatedAt is more than a month after createdAt, the conversation
 *    was actively used later → show updatedAt month.
 *  - Otherwise show createdAt month.
 *
 * Result format: "Month Year"  (e.g. "October 2025")
 */
export function formatConversationDateForSearch(createdAt: string, updatedAt: string): string {
  const created = new Date(createdAt);
  const updated = new Date(updatedAt);

  // Difference in months (can be fractional; >1 = more than one calendar month apart)
  const monthsDiff =
    (updated.getFullYear() - created.getFullYear()) * 12 +
    (updated.getMonth() - created.getMonth());

  const target = monthsDiff > 1 ? updated : created;
  return target.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}

/**
 * Strip citation markers like [1], [2] etc. and remove markdown formatting.
 * Returns plain text suitable for clipboard copy.
 */
export function stripMarkdownAndCitations(text: string): string {
  return text
    // Remove citation markers e.g. [1], [2]
    .replace(/\s*\[\d+\]/g, '')
    // Remove heading markers
    .replace(/^#{1,6}\s+/gm, '')
    // Remove bold/italic markers
    .replace(/\*{1,3}(.*?)\*{1,3}/g, '$1')
    // Remove inline code backticks
    .replace(/`([^`]+)`/g, '$1')
    // Remove links, keep text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    // Remove horizontal rules
    .replace(/^---+$/gm, '')
    // Collapse multiple newlines
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * Format chatMode for display label (capitalize first letter).
 * Handles API form `agent:<strategy>` from agent streams.
 */
export function formatChatMode(chatMode?: string): string {
  if (!chatMode) return '';
  if (chatMode.startsWith('agent:')) {
    const strategy = chatMode.slice('agent:'.length);
    // API uses `verification`; UI label reads as “Verify”
    const label =
      strategy === 'verification'
        ? 'Verify'
        : strategy.length > 0
          ? strategy.charAt(0).toUpperCase() + strategy.slice(1)
          : strategy;
    return `Agent (${label})`;
  }
  return chatMode
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Format a Unix timestamp (ms) as a relative time string.
 * e.g. "Just now", "3 minutes ago", "2 hours ago", "5 days ago", "3 Nov 2025"
 */
export function formatRelativeTime(timestamp?: number | null): string {
  if (!timestamp) return '-';
  const now = Date.now();
  const diff = now - timestamp;
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  const weeks = Math.floor(days / 7);

  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });

  if (seconds < 60) return rtf.format(-seconds, 'second');
  if (minutes < 60) return rtf.format(-minutes, 'minute');
  if (hours < 24) return rtf.format(-hours, 'hour');
  if (days < 7) return rtf.format(-days, 'day');

  if (weeks < 4) return rtf.format(-weeks, 'week');

  const date = new Date(timestamp);
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/**
 * Format a Unix timestamp (ms) as a short month+day string.
 * e.g. "Nov 3"
 */
export function formatEnabledDate(timestamp?: number): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
}

/*
 * Converts a raw name to a slug suitable for icon filenames.
 * Lowercases everything and replaces spaces / special characters with hyphens.
 *
 * Examples:
 *   toIconSlug('openAI')        → 'openai'
 *   toIconSlug('Google Gemini') → 'google-gemini'
 *   toIconSlug('anthropic')     → 'anthropic'
 */
export function toIconSlug(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[\s_.,/\\]+/g, '-')  // spaces / separators → hyphen
    .replace(/[^a-z0-9-]/g, '')    // strip remaining non-alphanumeric
    .replace(/-+/g, '-')            // collapse consecutive hyphens
    .replace(/^-+|-+$/g, '');       // trim leading / trailing hyphens
}

/**
 * Returns the canonical public path for an icon.
 *
 * @param subfolder - The subfolder inside public/icons/ (e.g. 'logos', 'connectors')
 * @param name      - The raw name to slugify (e.g. 'openAI', 'Google Gemini')
 * @returns Path like `/icons/logos/openai.svg`
 */
export function toIconPath(subfolder: string, name: string): string {
  return `/icons/${subfolder}/${toIconSlug(name)}.svg`;
}
