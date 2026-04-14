'use client';

import { Theme } from '@radix-ui/themes';
import { useEffect, useState, useCallback, createContext, useContext } from 'react';

const THEME_STORAGE_KEY = 'pipeshub-theme-preference';

type Appearance = 'light' | 'dark';

/**
 * User preference: 'system' follows the OS setting,
 * 'light' / 'dark' are explicit overrides.
 */
export type ThemePreference = 'system' | 'light' | 'dark';

interface ThemeContextValue {
  appearance: Appearance;
  preference: ThemePreference;
  setPreference: (pref: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  appearance: 'light',
  preference: 'system',
  setPreference: () => {},
});

export function useThemeAppearance() {
  return useContext(ThemeContext);
}

interface ThemeProviderProps {
  children: React.ReactNode;
}

function resolveAppearance(pref: ThemePreference): Appearance {
  if (pref === 'light' || pref === 'dark') return pref;
  if (typeof window === 'undefined') return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
}

function readStoredPreference(): ThemePreference {
  if (typeof window === 'undefined') return 'system';
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  return 'system';
}

/**
 * Blocking inline script that runs before any paint.
 * Reads the stored theme preference from localStorage, resolves it to
 * light / dark, and sets the correct CSS class + colorScheme on <html>.
 * This ensures the page background and CSS-variable-driven styles are
 * correct from the very first frame — no flash of wrong theme.
 */
export function ThemeScript() {
  return (
    <script
      dangerouslySetInnerHTML={{
        __html: `(function(){try{var k='pipeshub-theme-preference',p=localStorage.getItem(k)||'system',d=p==='dark'||(p==='system'&&window.matchMedia('(prefers-color-scheme:dark)').matches);document.documentElement.classList.add(d?'dark':'light');document.documentElement.style.colorScheme=d?'dark':'light'}catch(e){}})()`,
      }}
    />
  );
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [mounted, setMounted] = useState(false);
  const [preference, setPreferenceState] = useState<ThemePreference>('system');
  const [appearance, setAppearance] = useState<Appearance>('light');

  // After mount: read localStorage (blocking), resolve, and enable the real render.
  // This avoids hydration mismatch — SSG HTML always uses the safe defaults above,
  // and the inline <ThemeScript> handles visual correctness before React kicks in.
  useEffect(() => {
    const pref = readStoredPreference();
    const resolved = resolveAppearance(pref);
    setPreferenceState(pref);
    setAppearance(resolved);
    setMounted(true);
  }, []);

  // Listen for OS theme changes when user prefers 'system'
  useEffect(() => {
    if (!mounted || preference !== 'system') return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      setAppearance(e.matches ? 'dark' : 'light');
    };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [mounted, preference]);

  const setPreference = useCallback((pref: ThemePreference) => {
    localStorage.setItem(THEME_STORAGE_KEY, pref);
    setPreferenceState(pref);
    setAppearance(resolveAppearance(pref));
  }, []);

  // Keep --page-background in sync (overrides the CSS-class value with
  // the JS-resolved value so it always matches the Radix Theme appearance).
  useEffect(() => {
    if (!mounted) return;
    document.documentElement.style.setProperty(
      '--page-background',
      appearance === 'dark' ? '#111113' : '#dcdcdc'
    );
    // Keep the html class in sync when user toggles theme within the app
    document.documentElement.classList.remove('light', 'dark');
    document.documentElement.classList.add(appearance);
    document.documentElement.style.colorScheme = appearance;
  }, [mounted, appearance]);

  // Before mount: return null — the inline <ThemeScript> already set the
  // correct background / class on <html>, so the user sees the right color.
  // This phase is ~16ms (one React commit cycle).
  if (!mounted) return null;

  return (
    <ThemeContext.Provider value={{ appearance, preference, setPreference }}>
      <Theme
        accentColor="jade"
        grayColor="olive"
        appearance={appearance}
        radius="medium"
        data-accent-color="emerald"
      >
        {children}
      </Theme>
    </ThemeContext.Provider>
  );
}
