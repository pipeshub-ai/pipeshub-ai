'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

const COPIED_FLAG_MS = 2000;

/**
 * Copies text and raises `copied` for a moment. `navigator.clipboard` only exists on secure pages
 * (HTTPS or localhost), so on a plain-HTTP install `copy` does nothing and resolves false.
 */
export function useCopyText(): { copied: boolean; copy: (text: string) => Promise<boolean> } {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    },
    [],
  );

  const copy = useCallback(async (text: string) => {
    if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return false;
    }
    setCopied(true);
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopied(false), COPIED_FLAG_MS);
    return true;
  }, []);

  return { copied, copy };
}
