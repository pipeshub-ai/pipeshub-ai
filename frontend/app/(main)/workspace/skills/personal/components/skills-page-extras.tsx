import type React from 'react';
import type { SkillTrustStanding } from '../types';

/** A header action an edition adds to the skills page, and the panel it opens. */
export interface SkillsPageAction {
  label: string;
  icon: string;
  /** Shown on the button; 0 hides the action. */
  badge: number;
  onSelect: () => void;
  panel: React.ReactNode;
}

/**
 * The action an edition adds to the skills page, or null (OSS: none). Called on every render; `onClosed` reloads
 * the page's list once the panel closes.
 */
export function useSkillsPageAction(_options: { onClosed: () => void }): SkillsPageAction | null {
  return null;
}

/**
 * The i18n key of why agents can't use an imported skill, where an edition words it for its own review states; null
 * keeps the card's own wording (OSS: always null).
 */
export function skillReviewNoticeKey(_standing: SkillTrustStanding): string | null {
  return null;
}
