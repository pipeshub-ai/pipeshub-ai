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
 * put them back over it. So a load is ignored while a save is pending, every save moves
 * `revision` when it starts and again when it settles, and a load applies its settings only if
 * `revision` has not moved since it began.
 *
 * A load that could not read the settings (`undefined`) keeps the last confirmed ones; until one
 * read has succeeded, `hasLoaded` is false and the page must not offer them for editing.
 */
export function useImageSettings({ save, onSaved, onFailed }: UseImageSettingsOptions) {
  const [settings, setSettings] = useState<WebSearchSettings>(DEFAULT_IMAGE_SETTINGS);
  const [isSaving, setIsSaving] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const revision = useRef(0);
  const savesPending = useRef(0);

  /** Call when a load starts; hand the result to `applyLoaded` with the load's settings. */
  const beginLoad = useCallback(() => revision.current, []);

  const applyLoaded = useCallback((loaded: WebSearchSettings | undefined, loadStartedAt: number) => {
    if (!loaded || savesPending.current > 0 || revision.current !== loadStartedAt) return;
    setSettings(loaded);
    setHasLoaded(true);
  }, []);

  const update = useCallback(
    async (next: WebSearchSettings, successMessage: string) => {
      revision.current += 1;
      savesPending.current += 1;
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
        savesPending.current -= 1;
        revision.current += 1;
        setIsSaving(false);
      }
    },
    [settings, save, onSaved, onFailed],
  );

  return { settings, isSaving, hasLoaded, beginLoad, applyLoaded, update };
}
