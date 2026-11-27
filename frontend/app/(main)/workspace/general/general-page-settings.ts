import type { ReactNode } from 'react';

/**
 * Settings an edition adds to the admin General page that load, show and save with its form.
 * The page owns none of them: it waits for `ready`, shows `section`, and saves and discards
 * through here. Editions plug in via config.ts (`useGeneralPageSettings`).
 */
export interface GeneralPageSettings {
  /** The saved values are known (or there are none); the page waits for this before it shows the form. */
  ready: boolean;
  /** The saved values could not be read; `retry` reads them again. */
  failed: boolean;
  retry: () => void;
  /** Something here differs from what is saved. */
  dirty: boolean;
  /** Fields these settings add to the organization update the page sends. */
  orgFields: Readonly<Record<string, unknown>>;
  /** Saves what changed; called after the organization update went through. */
  save: () => Promise<void>;
  discard: () => void;
  /** What to show below the page's own sections; null when there is nothing. */
  section: ReactNode;
}

const NO_SETTINGS: GeneralPageSettings = {
  ready: true,
  failed: false,
  retry: () => undefined,
  dirty: false,
  orgFields: {},
  save: async () => undefined,
  discard: () => undefined,
  section: null,
};

/**
 * For an edition that adds no settings: nothing to load, show or save.
 * `active` is true once the viewer is known to be an admin.
 */
export function useNoGeneralPageSettings(_active: boolean): GeneralPageSettings {
  return NO_SETTINGS;
}
