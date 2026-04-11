'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Language } from '@/lib/i18n/supported-languages';

export type { Language };

interface LanguageState {
  language: Language;
  setLanguage: (lang: Language) => void;
}

export const useLanguageStore = create<LanguageState>()(
  persist(
    (set) => ({
      language: 'en-US',
      setLanguage: (lang) => {
        // Change i18n language
        if (typeof window !== 'undefined') {
          // Dynamic import to avoid SSR issues
          import('@/lib/i18n/config').then((module) => {
            module.default.changeLanguage(lang);
          });
        }
        set({ language: lang });
      },
    }),
    {
      name: 'pipeshub-lang-state',
      storage: createJSONStorage(() => localStorage),
    }
  )
);
