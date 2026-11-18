'use client';

import { useCallback, useRef, useState } from 'react';
import type { WebSearchSettings } from './types';

export const DEFAULT_IMAGE_SETTINGS: WebSearchSettings = { includeImages: false, maxImages: 3 };

interface UseImageSettingsOptions {
  /** Stores the settings and returns them as the server now holds them. */
  save: (next: WebSearchSettings) => Promise<WebSearchSettings>;
  onSaved: (message: string) => void;
  onFailed: (error: unknown) => void;
}

/**
 * "Send images to LLM" settings, shown optimistically while a save runs.
 *
 * The page also reloads the whole web-search config (after provider changes). A reload that was
 * in flight while a save started or finished carries settings older than the save, and must not
 * put them back over it. So every save moves `revision` when it starts and again when it
 * settles, and a load applies its settings only if `revision` has not moved since it began.
 */
export function useImageSettings({ save, onSaved, onFailed }: UseImageSettingsOptions) {
  const [settings, setSettings] = useState<WebSearchSettings>(DEFAULT_IMAGE_SETTINGS);
  const [isSaving, setIsSaving] = useState(false);
  const revision = useRef(0);

  /** Call when a load starts; hand the result to `applyLoaded` with the load's settings. */
  const beginLoad = useCallback(() => revision.current, []);

  const applyLoaded = useCallback((loaded: WebSearchSettings, loadStartedAt: number) => {
    if (revision.current === loadStartedAt) setSettings(loaded);
  }, []);

  const update = useCallback(
    async (next: WebSearchSettings, successMessage: string) => {
      revision.current += 1;
      const previous = settings;
      setSettings(next);
      setIsSaving(true);
      try {
        setSettings(await save(next));
        onSaved(successMessage);
      } catch (error) {
        setSettings(previous);
        onFailed(error);
      } finally {
        revision.current += 1;
        setIsSaving(false);
      }
    },
    [settings, save, onSaved, onFailed],
  );

  return { settings, isSaving, beginLoad, applyLoaded, update };
}
